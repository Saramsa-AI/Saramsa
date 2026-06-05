import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'apis.settings')
django.setup()

from django.contrib.auth import get_user_model
from feedback_analysis.models import Insight, Analysis
from datetime import datetime, timedelta

User = get_user_model()

# Find user
target_email = 'lathumahi24@gmail.com'
user = User.objects.filter(email=target_email).first()

print(f"Looking for: {target_email}")
print(f"User: {user.email if user else 'Not found'}")
print(f"User ID: {user.id if user else 'N/A'}")

if user:
    # Get recent insights (analyses)
    insights = Insight.objects.filter(user=user).order_by('-created_at')[:10]
    print(f"\n📋 Recent analyses ({insights.count()}):")
    for ins in insights:
        print(f"  ✅ {str(ins.id)[:30]}... | {ins.created_at.strftime('%Y-%m-%d %H:%M')} | Status: {ins.status}")

    # Get today's uploads
    today = datetime.now().date()
    today_insights = Insight.objects.filter(
        user=user,
        created_at__date=today
    ).order_by('-created_at')

    print(f"\n🆕 Today's uploads ({today_insights.count()}):")
    for ins in today_insights:
        print(f"  ✅ {str(ins.id)[:30]}... | {ins.created_at.strftime('%H:%M:%S')} | Status: {ins.status}")
else:
    print("\n❌ User not found in database!")
