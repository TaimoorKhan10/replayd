"""
Fixed commerce route agent for the replayd commerce route demo.

The fixed behavior is deterministic: an image request is routed to
send_product_image.
"""

IMAGE_REQUEST = "Can I see a picture?"
FIXED_ROUTE = "send_product_image"


def route_request(user_input: str) -> dict:
    """Return the route selected by the fixed agent."""
    if user_input == IMAGE_REQUEST:
        return {
            "route": FIXED_ROUTE,
            "reason": "User asked to see a picture, so route to product image delivery.",
        }

    return {
        "route": "text_only",
        "reason": "Default text response route.",
    }
