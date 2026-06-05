#!/usr/bin/env python3
"""
Performance test script for PR #73 fixes
Uploads test files and monitors processing time
"""
import os
import sys
import time
import requests
from pathlib import Path

# Configuration
API_BASE = os.getenv('API_URL', 'https://saramsa-api-prod.azurewebsites.net')
AUTH_TOKEN = os.getenv('AUTH_TOKEN')  # Set this via environment variable
PROJECT_ID = os.getenv('PROJECT_ID', 'ad8c48fd-5602-40db-a544-06de3a411a09')

TEST_FILES = [
    "Saramsa-Data/tickertape_customer_feedback (1).csv",  # 200 comments
    "Saramsa-Data/Booktask (1).csv",  # ~800+ comments
]

def upload_file(file_path):
    """Upload a file and return the task ID"""
    if not AUTH_TOKEN:
        print("❌ AUTH_TOKEN environment variable not set!")
        print("   Set it with: export AUTH_TOKEN='your-token-here'")
        sys.exit(1)

    headers = {
        'Authorization': f'Bearer {AUTH_TOKEN}',
    }

    files = {
        'file': open(file_path, 'rb'),
    }

    data = {
        'project_id': PROJECT_ID,
    }

    print(f"\n📤 Uploading: {Path(file_path).name}")
    print(f"   Size: {os.path.getsize(file_path) / 1024:.1f} KB")

    response = requests.post(
        f'{API_BASE}/api/insights/ingest/',
        headers=headers,
        files=files,
        data=data,
    )

    if response.status_code == 200 or response.status_code == 201:
        result = response.json()
        task_id = result.get('data', {}).get('task_id')
        print(f"✅ Upload successful! Task ID: {task_id}")
        return task_id
    else:
        print(f"❌ Upload failed: {response.status_code}")
        print(f"   Response: {response.text}")
        return None

def monitor_task(task_id):
    """Monitor task progress and report timing"""
    if not AUTH_TOKEN:
        return

    headers = {
        'Authorization': f'Bearer {AUTH_TOKEN}',
    }

    start_time = time.time()
    last_status = None

    print(f"\n⏳ Monitoring task: {task_id}")

    while True:
        try:
            response = requests.get(
                f'{API_BASE}/api/insights/task-status/{task_id}/',
                headers=headers,
            )

            if response.status_code == 404:
                print(f"❌ Task not found (404) - may have been killed")
                break

            if response.status_code == 200:
                data = response.json().get('data', {})
                status = data.get('status')

                if status != last_status:
                    elapsed = time.time() - start_time
                    print(f"   [{elapsed:.0f}s] Status: {status}")
                    last_status = status

                if status in ['SUCCESS', 'PARTIAL', 'FAILED', 'CANCELLED']:
                    elapsed = time.time() - start_time
                    minutes = int(elapsed // 60)
                    seconds = int(elapsed % 60)

                    if status == 'SUCCESS':
                        print(f"\n✅ COMPLETED in {elapsed:.1f}s ({minutes}m {seconds}s)")
                        print(f"   Analysis ID: {data.get('result', {}).get('insight_id')}")
                    elif status == 'FAILED':
                        print(f"\n❌ FAILED after {elapsed:.1f}s")
                        print(f"   Error: {data.get('error')}")
                    elif status == 'CANCELLED':
                        print(f"\n⚠️  CANCELLED after {elapsed:.1f}s")

                    return status, elapsed

            time.sleep(5)  # Poll every 5 seconds

        except KeyboardInterrupt:
            print("\n⚠️  Monitoring interrupted")
            break
        except Exception as e:
            print(f"❌ Error monitoring task: {e}")
            time.sleep(5)

    return None, None

def main():
    print("=" * 60)
    print("PERFORMANCE TEST - PR #73 Fixes")
    print("=" * 60)
    print(f"API: {API_BASE}")
    print(f"Project: {PROJECT_ID}")
    print(f"Files: {len(TEST_FILES)}")
    print()

    results = []

    for file_path in TEST_FILES:
        if not os.path.exists(file_path):
            print(f"⚠️  File not found: {file_path}")
            continue

        # Count lines
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            line_count = sum(1 for _ in f) - 1  # Subtract header

        print(f"\n{'=' * 60}")
        print(f"Test File: {Path(file_path).name}")
        print(f"Comments: ~{line_count}")
        print(f"Expected Time: 10-15 minutes (vs 40 min before fix)")
        print(f"{'=' * 60}")

        task_id = upload_file(file_path)

        if task_id:
            status, elapsed = monitor_task(task_id)
            if status and elapsed:
                results.append({
                    'file': Path(file_path).name,
                    'comments': line_count,
                    'status': status,
                    'time': elapsed,
                })

        # Wait between uploads
        if file_path != TEST_FILES[-1]:
            print("\n⏸️  Waiting 30s before next upload...")
            time.sleep(30)

    # Summary
    print(f"\n\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")

    for result in results:
        minutes = int(result['time'] // 60)
        seconds = int(result['time'] % 60)
        status_icon = '✅' if result['status'] == 'SUCCESS' else '❌'
        print(f"{status_icon} {result['file']}")
        print(f"   {result['comments']} comments | {minutes}m {seconds}s | {result['status']}")

    print(f"\n{'=' * 60}\n")

if __name__ == '__main__':
    main()
