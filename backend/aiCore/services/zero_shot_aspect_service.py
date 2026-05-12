"""
Zero-Shot NLI Aspect Classification Service

Replaces cosine-similarity aspect classification with zero-shot natural language
inference using DeBERTa-v3. Dramatically improves mapping rate from ~8% to ~80-85%
while staying fully local with no API costs.

Model: MoritzLaurer/deberta-v3-base-zeroshot-v2.0 (configurable via NLI_ASPECT_MODEL)

Backend selection (NLI_BACKEND env var or auto-detect):
  - gpu:  PyTorch FP16 on CUDA (fastest with GPU)
  - onnx: ONNX Runtime FP32 on CPU (1.4x faster than PyTorch CPU)
  - cpu:  PyTorch FP32 on CPU (fallback)

Note: INT8 quantization is NOT supported for DeBERTa-v3 NLI — it destroys
classification accuracy (scores collapse to <0.35 with wrong rankings).
"""

import logging
import os
import time
import threading
from typing import List, Dict, Any, Optional, Callable

import psutil
import torch
from transformers import pipeline


def _onnx_available() -> bool:
    """Check if optimum + onnxruntime are installed."""
    try:
        from optimum.onnxruntime import ORTModelForSequenceClassification  # noqa: F401
        return True
    except ImportError:
        return False


class TaskCancelled(Exception):
    """Raised when a Celery task is revoked mid-processing."""
    pass

logger = logging.getLogger(__name__)


class ZeroShotAspectService:
    """
    Zero-shot NLI aspect classification using DeBERTa-v3.

    Drop-in replacement for SimilarityAspectService with the same
    classify_aspects() interface and return format.

    Environment variables:
        NLI_ASPECT_MODEL: HuggingFace model ID (default: MoritzLaurer/deberta-v3-base-zeroshot-v2.0)
        NLI_ASPECT_THRESHOLD: Minimum entailment score (default: 0.60)
        NLI_MAX_ASPECTS_PER_COMMENT: Max aspects per comment (default: 2)
        NLI_BATCH_SIZE: Comments per pipeline batch (default: 32)
        NLI_BACKEND: Force backend — "gpu", "onnx", or "cpu" (default: auto-detect)
    """

    def __init__(self):
        self.MODEL_NAME = os.getenv(
            "NLI_ASPECT_MODEL",
            "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
        )
        # 0.50 balances precision and recall for DeBERTa-v3 entailment:
        # captures genuine aspect mentions while filtering noise.
        self._threshold = float(os.getenv("NLI_ASPECT_THRESHOLD", "0.50"))
        self._max_aspects = int(os.getenv("NLI_MAX_ASPECTS_PER_COMMENT", "2"))
        self._batch_size = int(os.getenv("NLI_BATCH_SIZE", "32"))
        self._pipeline = None
        self._dtype = None
        self._device_name = "not_loaded"
        self._backend = "not_loaded"
        self._initialize_model()

    # ── Diagnostics helpers ──────────────────────────────────────────────

    def _log_system_memory(self, label: str) -> None:
        """Log current system RAM usage."""
        ram = psutil.virtual_memory()
        logger.info(
            f"[DIAG] {label} | RAM: {ram.used / 1024**3:.1f}GB / "
            f"{ram.total / 1024**3:.1f}GB ({ram.percent}%) | "
            f"Available: {ram.available / 1024**3:.1f}GB"
        )

    def _log_gpu_memory(self, label: str) -> None:
        """Log current GPU VRAM usage. No-op on CPU."""
        if not torch.cuda.is_available():
            return
        free, total = torch.cuda.mem_get_info(0)
        allocated = torch.cuda.memory_allocated(0)
        reserved = torch.cuda.memory_reserved(0)
        logger.info(
            f"[DIAG] {label} | VRAM: allocated={allocated / 1024**2:.0f}MB, "
            f"reserved={reserved / 1024**2:.0f}MB, "
            f"free={free / 1024**2:.0f}MB / {total / 1024**2:.0f}MB"
        )

    def _verify_model_device(self) -> None:
        """Check that model weights are actually on the expected device."""
        if self._backend == "onnx-cpu":
            # ONNX Runtime models don't expose PyTorch parameters
            logger.info(
                f"[DIAG] Model weight verification | "
                f"backend={self._backend} (ONNX Runtime session, no PyTorch weights)"
            )
            return

        model = self._pipeline.model
        param = next(model.parameters())
        actual_device = str(param.device)
        actual_dtype = str(param.dtype)
        logger.info(
            f"[DIAG] Model weight verification | "
            f"device={actual_device}, dtype={actual_dtype} "
            f"(expected: {self._device_name}, "
            f"{'fp16' if self._dtype == torch.float16 else 'fp32'})"
        )
        if "cuda" in self._device_name and "cuda" not in actual_device:
            logger.warning(
                "[DIAG] !! Model is on CPU despite CUDA being available — "
                "inference will be slow!"
            )

    # ── Model loading ──────────────────────────────────────────────────

    def _select_backend(self) -> str:
        """Decide which backend to use: gpu, onnx, or cpu.

        Priority: NLI_BACKEND env var override > auto-detect.
        Auto-detect: GPU if CUDA available, else ONNX FP32 if installed, else CPU.
        Skips ONNX if available RAM < 2GB to avoid OOM during export.

        Note: INT8 quantization is NOT offered here — DeBERTa-v3 NLI accuracy
        collapses under INT8 dynamic quantization (scores drop from ~1.0 to <0.35).
        """
        forced = os.getenv("NLI_BACKEND", "").strip().lower()
        if forced in ("gpu", "onnx", "cpu"):
            logger.info(f"[DIAG] NLI_BACKEND override: {forced}")
            return forced

        if torch.cuda.is_available():
            return "gpu"

        avail_gb = psutil.virtual_memory().available / (1024 ** 3)
        if avail_gb < 2.0:
            logger.info(
                f"[DIAG] Skipping ONNX for NLI — only {avail_gb:.1f}GB RAM available. "
                f"Falling back to PyTorch CPU."
            )
            return "cpu"

        if _onnx_available():
            return "onnx"
        return "cpu"

    def _initialize_model(self) -> None:
        """Load the zero-shot classification pipeline.

        Backend selection:
          GPU available  → PyTorch FP16 on CUDA
          ONNX installed → ONNX Runtime on CPU (2-4x faster than PyTorch CPU)
          fallback       → PyTorch FP32 on CPU
        """
        try:
            logger.info(f"{'='*60}")
            logger.info(f"Loading zero-shot NLI model: {self.MODEL_NAME}")
            self._log_system_memory("Pre-load")

            backend = self._select_backend()

            if backend == "gpu":
                self._init_pytorch_gpu()
            elif backend == "onnx":
                self._init_onnx_cpu()
            else:
                self._init_pytorch_cpu()

            self._log_system_memory("Post-load")
            self._verify_model_device()

            # Warm-up inference (also validates the model works end-to-end)
            logger.info("[DIAG] Running warm-up inference...")
            warmup_start = time.time()
            test = self._pipeline(
                "test comment",
                candidate_labels=["quality"],
                hypothesis_template="This customer feedback is about {}.",
            )
            warmup_time = time.time() - warmup_start
            logger.info(f"[DIAG] Warm-up completed in {warmup_time:.2f}s")

            if not test or "labels" not in test:
                raise RuntimeError("Model warm-up failed - unexpected output format")

            if self._backend == "pytorch-gpu-fp16":
                self._log_gpu_memory("Post-warmup")
                peak_mb = torch.cuda.max_memory_allocated(0) / 1024**2
                logger.info(f"[DIAG] Peak GPU memory during load+warmup: {peak_mb:.0f}MB")

            logger.info(
                f"Successfully loaded {self.MODEL_NAME} "
                f"(backend: {self._backend}, device: {self._device_name}, "
                f"batch_size: {self._batch_size}, threshold: {self._threshold})"
            )
            logger.info(f"{'='*60}")
        except Exception as e:
            error_msg = f"Failed to load NLI model {self.MODEL_NAME}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _init_pytorch_gpu(self) -> None:
        """Load model with PyTorch FP16 on CUDA."""
        self._dtype = torch.float16
        self._device_name = "cuda"
        self._backend = "pytorch-gpu-fp16"

        gpu_name = torch.cuda.get_device_name(0)
        props = torch.cuda.get_device_properties(0)
        logger.info(
            f"[DIAG] GPU: {gpu_name} | VRAM: {props.total_memory / 1024**3:.1f}GB | "
            f"Compute: {props.major}.{props.minor} | Loading in FP16"
        )
        self._log_gpu_memory("Pre-load")
        torch.cuda.reset_peak_memory_stats(0)

        load_start = time.time()
        self._pipeline = pipeline(
            "zero-shot-classification",
            model=self.MODEL_NAME,
            device=0,
            torch_dtype=self._dtype,
            multi_label=True,
        )
        load_time = time.time() - load_start
        logger.info(f"[DIAG] Pipeline created in {load_time:.1f}s")

        torch.cuda.empty_cache()
        self._log_gpu_memory("Post-load (after cache clear)")

    def _init_onnx_cpu(self) -> None:
        """Load model with ONNX Runtime on CPU.

        Uses optimum's ORTModelForSequenceClassification which auto-exports
        the model to ONNX format on first run (cached afterwards).
        Gives 2-4x speedup over PyTorch FP32 on CPU through graph
        optimizations, operator fusion, and better memory management.
        """
        from optimum.onnxruntime import ORTModelForSequenceClassification

        self._dtype = None  # ONNX Runtime manages its own types
        self._device_name = "cpu"
        self._backend = "onnx-cpu"

        logger.info(
            "[DIAG] Loading model with ONNX Runtime on CPU "
            "(export=True, cached after first run)"
        )

        load_start = time.time()
        ort_model = ORTModelForSequenceClassification.from_pretrained(
            self.MODEL_NAME,
            export=True,
        )
        self._pipeline = pipeline(
            "zero-shot-classification",
            model=ort_model,
            tokenizer=self.MODEL_NAME,
            device=-1,
            multi_label=True,
        )
        load_time = time.time() - load_start
        logger.info(f"[DIAG] ONNX pipeline created in {load_time:.1f}s")

    def _init_pytorch_cpu(self) -> None:
        """Load model with PyTorch FP32 on CPU (fallback)."""
        self._dtype = torch.float32
        self._device_name = "cpu"
        self._backend = "pytorch-cpu-fp32"

        logger.info("[DIAG] No GPU, no ONNX Runtime — loading model in FP32 on CPU")

        load_start = time.time()
        self._pipeline = pipeline(
            "zero-shot-classification",
            model=self.MODEL_NAME,
            device=-1,
            torch_dtype=self._dtype,
            multi_label=True,
        )
        load_time = time.time() - load_start
        logger.info(f"[DIAG] Pipeline created in {load_time:.1f}s")

    def classify_aspects(
        self,
        comments: List[str],
        aspects: List[str],
        run_id: Optional[str] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Classify comments against aspects using zero-shot NLI.

        Uses true batching — passes lists of comments to the pipeline so
        the underlying model can batch (text, hypothesis) pairs together
        for much better throughput on both CPU and GPU.

        Args:
            comments: List of comment strings to classify
            aspects: List of aspect names to classify against
            run_id: Optional run identifier for logging
            is_cancelled: Optional callback that returns True if the task
                          has been revoked. Checked between batches for
                          cooperative cancellation (needed on Windows
                          where SIGTERM doesn't work).

        Returns:
            List of classification results, one per comment

        Raises:
            TaskCancelled: If is_cancelled() returns True between batches
        """
        if not comments or not aspects:
            logger.warning("Empty comments or aspects provided to classify_aspects")
            return []

        use_gpu = self._backend == "pytorch-gpu-fp16"
        total = len(comments)
        n_aspects = len(aspects)
        total_pairs = total * n_aspects

        logger.info(f"{'─'*60}")
        logger.info(
            f"Classifying {total} comments × {n_aspects} aspects = "
            f"{total_pairs} NLI pairs (run: {run_id})"
        )
        logger.info(
            f"[DIAG] Config: batch_size={self._batch_size}, "
            f"threshold={self._threshold}, backend={self._backend}"
        )
        self._log_system_memory("Inference start")
        if use_gpu:
            self._log_gpu_memory("Inference start")
            torch.cuda.reset_peak_memory_stats(0)

        start_time = time.time()

        hypothesis_template = "This customer feedback is about {}."

        # --- Deduplicate identical comments to avoid redundant inference ---
        unique_texts = list(dict.fromkeys(comments))  # preserves order
        dedup_saved = total - len(unique_texts)
        if dedup_saved > 0:
            logger.info(
                f"[DIAG] Deduplication: {len(unique_texts)} unique texts "
                f"({dedup_saved} duplicates skipped, "
                f"saving {dedup_saved * n_aspects} NLI pairs)"
            )

        unique_results: Dict[str, Dict[str, Any]] = {}
        n_batches = (len(unique_texts) + self._batch_size - 1) // self._batch_size
        batch_times: list = []

        for batch_idx, batch_start in enumerate(range(0, len(unique_texts), self._batch_size)):
            # Cooperative cancellation check between batches
            if is_cancelled and is_cancelled():
                logger.info(f"NLI classification cancelled at {batch_start}/{len(unique_texts)} unique texts")
                raise TaskCancelled(f"Task revoked after {batch_start} unique texts")

            batch_end = min(batch_start + self._batch_size, len(unique_texts))
            batch_texts = unique_texts[batch_start:batch_end]
            batch_pairs = len(batch_texts) * n_aspects

            batch_t0 = time.time()

            try:
                batch_outputs = self._pipeline(
                    batch_texts,
                    candidate_labels=aspects,
                    hypothesis_template=hypothesis_template,
                    multi_label=True,
                    batch_size=self._batch_size,
                )

                # Single-item batch returns a dict, not a list
                if isinstance(batch_outputs, dict):
                    batch_outputs = [batch_outputs]

                for local_idx, output in enumerate(batch_outputs):
                    text = batch_texts[local_idx]
                    unique_results[text] = self._parse_output(output, text, 0)

            except Exception as e:
                logger.error(f"Batch NLI failed at offset {batch_start}: {e}")
                # Per-comment fallback: try each comment individually
                for text in batch_texts:
                    try:
                        single_output = self._pipeline(
                            text,
                            candidate_labels=aspects,
                            hypothesis_template=hypothesis_template,
                            multi_label=True,
                        )
                        unique_results[text] = self._parse_output(
                            single_output, text, 0
                        )
                    except Exception as inner_e:
                        logger.error(f"Individual NLI also failed for comment: {inner_e}")
                        unique_results[text] = {
                            "comment_id": 0,
                            "comment_text": text,
                            "matched_aspects": [],
                            "aspect_scores": {a: 0.0 for a in aspects},
                            "processing_method": "zero_shot_nli_error",
                        }

            batch_elapsed = time.time() - batch_t0
            batch_times.append(batch_elapsed)
            pairs_per_sec = batch_pairs / batch_elapsed if batch_elapsed > 0 else 0

            # Per-batch diagnostics
            processed = min(batch_end, len(unique_texts))
            total_elapsed = time.time() - start_time
            overall_rate = processed / total_elapsed if total_elapsed > 0 else 0
            remaining = len(unique_texts) - processed
            eta = remaining / overall_rate if overall_rate > 0 else 0

            gpu_note = ""
            if use_gpu:
                alloc = torch.cuda.memory_allocated(0) / 1024**2
                gpu_note = f" | VRAM: {alloc:.0f}MB"

            logger.info(
                f"[DIAG] Batch {batch_idx+1}/{n_batches}: "
                f"{len(batch_texts)} comments, {batch_pairs} pairs in {batch_elapsed:.2f}s "
                f"({pairs_per_sec:.0f} pairs/s) | "
                f"Progress: {processed}/{len(unique_texts)} ({processed/len(unique_texts):.0%}) | "
                f"ETA: {eta:.0f}s{gpu_note}"
            )

        # --- Map results back to original comment order ---
        results = []
        for idx, comment in enumerate(comments):
            if comment in unique_results:
                result = unique_results[comment].copy()
            else:
                result = {
                    "comment_id": 0,
                    "comment_text": comment,
                    "matched_aspects": [],
                    "aspect_scores": {a: 0.0 for a in aspects},
                    "processing_method": "zero_shot_nli_skipped",
                }
            result["comment_id"] = idx
            results.append(result)

        # ── Final summary ──────────────────────────────────────────────
        processing_time = time.time() - start_time
        mapped = sum(1 for r in results if r["matched_aspects"])

        avg_batch = sum(batch_times) / len(batch_times) if batch_times else 0
        effective_pairs = len(unique_texts) * n_aspects
        overall_pairs_per_sec = effective_pairs / processing_time if processing_time > 0 else 0

        logger.info(f"{'─'*60}")
        logger.info(
            f"NLI COMPLETE: {processing_time:.2f}s total | "
            f"{mapped}/{total} comments mapped ({mapped/total:.0%})"
        )
        logger.info(
            f"[DIAG] Throughput: {overall_pairs_per_sec:.0f} pairs/s | "
            f"{len(unique_texts)/processing_time:.1f} comments/s"
        )
        logger.info(
            f"[DIAG] Batches: {len(batch_times)} total | "
            f"avg={avg_batch:.2f}s | min={min(batch_times):.2f}s | max={max(batch_times):.2f}s"
        )

        if use_gpu:
            peak_mb = torch.cuda.max_memory_allocated(0) / 1024**2
            self._log_gpu_memory("Inference end")
            logger.info(f"[DIAG] Peak GPU memory during inference: {peak_mb:.0f}MB")

        self._log_system_memory("Inference end")
        logger.info(f"{'─'*60}")

        return results

    def _parse_output(
        self, output: Dict, comment: str, comment_idx: int
    ) -> Dict[str, Any]:
        """Parse a single pipeline output into our result format."""
        aspect_scores = {
            label: float(score)
            for label, score in zip(output["labels"], output["scores"])
        }

        matched = sorted(
            [
                (label, score)
                for label, score in zip(output["labels"], output["scores"])
                if score >= self._threshold
            ],
            key=lambda x: x[1],
            reverse=True,
        )
        matched_aspects = [label for label, _ in matched[: self._max_aspects]]

        return {
            "comment_id": comment_idx,
            "comment_text": comment,
            "matched_aspects": matched_aspects,
            "aspect_scores": aspect_scores,
            "processing_method": "zero_shot_nli",
        }

    @property
    def is_initialized(self) -> bool:
        return self._pipeline is not None

    @property
    def model_info(self) -> Dict[str, Any]:
        dtype_str = "not_loaded"
        if self._backend == "onnx-cpu":
            dtype_str = "onnx-managed"
        elif self._dtype is not None:
            dtype_str = "fp16" if self._dtype == torch.float16 else "fp32"

        info = {
            "model_name": self.MODEL_NAME,
            "backend": self._backend,
            "device": self._device_name,
            "dtype": dtype_str,
            "is_initialized": self.is_initialized,
            "threshold": self._threshold,
            "max_aspects_per_comment": self._max_aspects,
            "batch_size": self._batch_size,
        }

        if self._backend == "pytorch-gpu-fp16" and self.is_initialized:
            info["gpu_name"] = torch.cuda.get_device_name(0)
            mem = torch.cuda.mem_get_info(0)
            info["gpu_vram_free_mb"] = round(mem[0] / 1024**2)
            info["gpu_vram_total_mb"] = round(mem[1] / 1024**2)

        return info


# Singleton accessor (thread-safe for concurrent Celery workers)
_zero_shot_aspect_service = None
_zero_shot_lock = threading.Lock()


def get_zero_shot_aspect_service() -> ZeroShotAspectService:
    """Get the singleton instance of ZeroShotAspectService."""
    global _zero_shot_aspect_service
    if _zero_shot_aspect_service is None:
        with _zero_shot_lock:
            if _zero_shot_aspect_service is None:
                _zero_shot_aspect_service = ZeroShotAspectService()
    return _zero_shot_aspect_service
