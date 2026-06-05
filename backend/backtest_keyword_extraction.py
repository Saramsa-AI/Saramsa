#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest keyword extraction with 2 files to verify the fix
"""
import os
import sys
import json
import time
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'apis.settings')
import django
django.setup()

from feedback_analysis.services.task_service import process_feedback_task
from django.contrib.auth import get_user_model
from feedback_analysis.models import Analysis

User = get_user_model()

def trigger_and_wait(file_path, user, project_id, max_wait=600):
    """Trigger task and wait for completion"""
    # Load file
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Handle both array of strings and array of objects
    if isinstance(data, list) and data:
        if isinstance(data[0], str):
            comments = [str(item).strip() for item in data if item and str(item).strip()]
        else:
            comments = [item.get('feedback', '') for item in data if item.get('feedback')]
    else:
        comments = []

    print(f"\n{'='*80}")
    print(f"Testing: {file_path.name}")
    print(f"Comments: {len(comments)}")
    print(f"{'='*80}")

    # Trigger task
    result = process_feedback_task.delay(
        comments=comments,
        company_name='Backtest Company',
        user_id_str=str(user.pk),
        project_id='ad8c48fd-5602-40db-a544-06de3a411a09',
        analysis_id=None,
        suggested_aspects=None,
        dimensions=None,
        force_regenerate=False
    )

    task_id = result.id
    print(f"Task ID: {task_id}")
    print(f"Waiting for completion (max {max_wait}s)...")

    # Wait for completion
    start = time.time()
    while time.time() - start < max_wait:
        status = result.status

        if status == 'SUCCESS':
            elapsed = time.time() - start
            print(f"✅ Completed in {int(elapsed)}s")

            # Get analysis result - find most recent analysis for this project/user
            # (Analysis model doesn't have celery_task_id field)
            analysis = Analysis.objects.filter(
                project_id=project_id,
                user=user
            ).order_by('-created_at').first()

            if analysis:
                data = analysis.result
                counts = data.get('counts', {})
                pos_keywords = data.get('positive_keywords', [])
                neg_keywords = data.get('negative_keywords', [])

                print(f"\n📊 Results:")
                print(f"  Total comments: {counts.get('total', 0)}")
                print(f"  Positive: {counts.get('positive', 0)}")
                print(f"  Negative: {counts.get('negative', 0)}")
                print(f"  Neutral: {counts.get('neutral', 0)}")
                print(f"\n🔑 Keywords:")
                print(f"  Positive keywords: {len(pos_keywords)} - {pos_keywords[:10]}")
                print(f"  Negative keywords: {len(neg_keywords)} - {neg_keywords[:10]}")

                if counts.get('negative', 0) > 0 and len(neg_keywords) == 0:
                    print(f"\n⚠️  BUG DETECTED: {counts['negative']} negative comments but 0 negative keywords!")
                elif counts.get('negative', 0) > 0 and len(neg_keywords) > 0:
                    print(f"\n✅ WORKING: {counts['negative']} negative comments → {len(neg_keywords)} keywords extracted")

                return {
                    'file': file_path.name,
                    'task_id': task_id,
                    'status': 'SUCCESS',
                    'counts': counts,
                    'positive_keywords_count': len(pos_keywords),
                    'negative_keywords_count': len(neg_keywords),
                    'elapsed': elapsed
                }
            else:
                print(f"⚠️  Analysis not found in database")
                return {'file': file_path.name, 'status': 'NO_RESULT', 'elapsed': elapsed}

        elif status == 'FAILURE':
            print(f"❌ Task failed")
            return {'file': file_path.name, 'status': 'FAILURE', 'elapsed': time.time() - start}

        time.sleep(5)

    print(f"⏱️  Timeout after {max_wait}s")
    return {'file': file_path.name, 'status': 'TIMEOUT', 'elapsed': max_wait}


def main():
    print("\n" + "="*80)
    print("BACKTEST: Keyword Extraction Quality Check")
    print("="*80)

    # Get user
    user = User.objects.filter(email='lathiesh.mahendran@corvusapp.com').first()
    if not user:
        user = User.objects.first()

    print(f"User: {user.email}")

    # Test files
    data_dir = Path("E:/RakeshProfessional/Saramsa-Web/Saramsa/Saramsa-Data")
    test_files = [
        data_dir / "Data-100.json",
        data_dir / "Data-200.json",
    ]

    results = []
    for file_path in test_files:
        if not file_path.exists():
            print(f"⚠️  File not found: {file_path}")
            continue

        result = trigger_and_wait(file_path, user, PROJECT_ID, max_wait=900)  # 15 min max
        results.append(result)

        print(f"\n{'='*80}\n")

        # Wait between uploads
        if file_path != test_files[-1]:
            print("⏸️  Waiting 30s before next test...\n")
            time.sleep(30)

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")

    for r in results:
        status_icon = '✅' if r['status'] == 'SUCCESS' else '❌'
        print(f"{status_icon} {r['file']}")
        if r['status'] == 'SUCCESS':
            counts = r.get('counts', {})
            print(f"   Negative comments: {counts.get('negative', 0)}")
            print(f"   Negative keywords: {r.get('negative_keywords_count', 0)}")

            if counts.get('negative', 0) > 0 and r.get('negative_keywords_count', 0) == 0:
                print(f"   ⚠️  BUG: No negative keywords extracted!")

    print(f"{'='*80}\n")

if __name__ == '__main__':
    main()
