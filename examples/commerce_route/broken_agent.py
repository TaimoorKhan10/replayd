"""
Broken commerce route agent for the replayd commerce route demo.

The bug is deterministic: an image request is routed to text_only instead of
send_product_image.
"""

IMAGE_REQUEST = "Can I see a picture?"
BROKEN_ROUTE = "text_only"


def route_request(user_input: str) -> dict:
    """Return the route selected by the broken agent."""
    if user_input == IMAGE_REQUEST:
        return {
            "route": BROKEN_ROUTE,
            "reason": "Answered with text instead of sending a product image.",
        }

    return {
        "route": "text_only",
        "reason": "Default text response route.",
    }
