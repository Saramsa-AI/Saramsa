"""
Domain Templates Library

Pre-built taxonomy templates for common business domains. Used to seed a new
project's taxonomy without an LLM call (saves ~30-60s on first upload).

The system identifies the domain from comments (via embedding similarity to
template descriptions or LLM identification) and seeds the project taxonomy
from the matching template. The taxonomy then evolves via additive growth as
more data comes in.
"""

from typing import Dict, List, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# Each template:
#   - aspects: pre-built list of aspect labels
#   - keywords: trigger words for fast text-based domain detection
#   - description: domain summary (used for semantic matching if needed)
DOMAIN_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "hospitality": {
        "description": "Hotels, restaurants, resorts, airlines, travel services",
        "keywords": [
            "hotel", "room", "stay", "check-in", "check-out", "reception",
            "restaurant", "menu", "food", "waiter", "ambience", "service",
            "housekeeping", "concierge", "lobby", "suite", "booking", "guest",
            "amenities", "wifi", "parking", "pool", "spa", "breakfast",
        ],
        "aspects": [
            "Room Cleanliness & Condition",
            "Staff Service & Hospitality",
            "Check-in & Front Desk",
            "Food & Beverage",
            "Amenities & Facilities",
            "Location & Accessibility",
            "Booking & Reservation",
            "Pricing & Value",
            "Noise & Comfort",
            "Wi-Fi & Connectivity",
        ],
    },
    "saas": {
        "description": "B2B and B2C software apps, productivity tools, platforms",
        "keywords": [
            "app", "feature", "bug", "crash", "login", "dashboard", "ui",
            "ux", "interface", "loading", "slow", "fast", "integration",
            "api", "subscription", "plan", "billing", "support", "ticket",
            "onboarding", "tutorial", "documentation", "settings",
            "performance", "sync",
        ],
        "aspects": [
            "User Interface & UX",
            "Performance & Speed",
            "Bugs & Stability",
            "Onboarding & Tutorials",
            "Pricing & Plans",
            "Customer Support",
            "Integrations & API",
            "Feature Requests",
            "Authentication & Login",
            "Documentation & Help",
        ],
    },
    "ecommerce": {
        "description": "Online retail, marketplaces, shopping platforms",
        "keywords": [
            "product", "order", "delivery", "shipping", "package", "return",
            "refund", "size", "quality", "price", "cart", "checkout",
            "payment", "card", "cod", "seller", "warehouse", "tracking",
            "stock", "discount", "coupon", "review",
        ],
        "aspects": [
            "Product Quality",
            "Pricing & Discounts",
            "Shipping & Delivery",
            "Returns & Refunds",
            "Order Tracking",
            "Payment & Checkout",
            "Customer Service",
            "Product Selection & Availability",
            "Seller Reliability",
            "Mobile App & Website Experience",
        ],
    },
    "fintech": {
        "description": "Trading, banking, investing, payments, crypto",
        "keywords": [
            "trading", "stock", "screener", "portfolio", "chart", "broker",
            "mutual fund", "sip", "investment", "trade", "market", "data",
            "alert", "watchlist", "kyc", "deposit", "withdraw", "wallet",
            "balance", "transaction", "upi", "bank", "loan", "credit",
            "rate", "interest",
        ],
        "aspects": [
            "Trading & Order Execution",
            "Screening & Discovery",
            "Charts & Technical Analysis",
            "Portfolio Tracking",
            "Data Quality & Accuracy",
            "Pricing & Brokerage",
            "Alerts & Notifications",
            "KYC & Onboarding",
            "Payments & Deposits",
            "Customer Support",
        ],
    },
    "healthcare": {
        "description": "Hospitals, clinics, telemedicine, pharmacy, health apps",
        "keywords": [
            "doctor", "appointment", "consultation", "clinic", "hospital",
            "prescription", "medicine", "pharmacy", "lab", "test", "report",
            "diagnosis", "patient", "nurse", "staff", "treatment", "billing",
            "insurance", "claim", "wait", "telemedicine", "video call",
        ],
        "aspects": [
            "Doctor & Care Quality",
            "Appointment & Scheduling",
            "Wait Times",
            "Staff & Nursing",
            "Facility & Cleanliness",
            "Pharmacy & Prescriptions",
            "Lab Tests & Reports",
            "Billing & Insurance",
            "Telemedicine Experience",
            "Customer Support",
        ],
    },
    "edtech": {
        "description": "Online courses, learning platforms, tutoring apps",
        "keywords": [
            "course", "lesson", "lecture", "video", "quiz", "assignment",
            "instructor", "teacher", "tutor", "student", "learning", "study",
            "exam", "certificate", "module", "syllabus", "live class",
            "recorded", "doubt", "mentor",
        ],
        "aspects": [
            "Course Content Quality",
            "Instructor & Teaching",
            "Video & Audio Quality",
            "Assignments & Quizzes",
            "Live Classes & Interaction",
            "Doubt Resolution",
            "Certificates & Credentials",
            "Pricing & Subscriptions",
            "Mobile App & Platform",
            "Customer Support",
        ],
    },
    "logistics": {
        "description": "Delivery, courier, food delivery, ride sharing",
        "keywords": [
            "delivery", "driver", "courier", "package", "pickup", "drop",
            "tracking", "address", "agent", "rider", "vehicle", "ride",
            "cab", "trip", "fare", "route", "eta", "late", "on-time",
        ],
        "aspects": [
            "Delivery Speed & ETA",
            "Driver & Rider Behavior",
            "Order Tracking",
            "Pickup & Drop Experience",
            "Pricing & Fares",
            "App Experience",
            "Customer Support",
            "Vehicle / Package Condition",
            "Cancellation & Refunds",
            "Payment Methods",
        ],
    },
}


def detect_domain_from_comments(comments: List[str], top_n: int = 1) -> List[Tuple[str, float]]:
    """Fast keyword-based domain detection.

    Args:
        comments: List of comment strings
        top_n: Return top N matching domains

    Returns:
        List of (domain_name, score) tuples sorted by score descending.
        Score is the fraction of keywords found in the comments.
    """
    if not comments:
        return []

    # Combine all comments into one searchable text (lowercased)
    full_text = " ".join(str(c).lower() for c in comments if c)
    if not full_text.strip():
        return []

    scores: List[Tuple[str, float]] = []
    for domain, template in DOMAIN_TEMPLATES.items():
        keywords = template.get("keywords", [])
        if not keywords:
            continue
        matches = sum(1 for kw in keywords if kw in full_text)
        score = matches / len(keywords)
        scores.append((domain, score))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_n]


def get_template_aspects(domain: str) -> Optional[List[str]]:
    """Get pre-built aspect list for a domain."""
    template = DOMAIN_TEMPLATES.get(domain.lower())
    return template.get("aspects") if template else None


def get_template(domain: str) -> Optional[Dict[str, Any]]:
    """Get full template metadata for a domain."""
    return DOMAIN_TEMPLATES.get(domain.lower())


def list_domains() -> List[str]:
    """List all available domain templates."""
    return list(DOMAIN_TEMPLATES.keys())
