import json
import os
import datetime
import re
from google import genai
from google.genai import types

# Load environment variables (e.g., GOOGLE_API_KEY)
def _load_support_data():
    """Helper function to load data from support-tickets.json."""
    try:
        with open('support-tickets.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"customers": [], "orders": [], "tickets": []}

def get_customer(name: str) -> dict:
    """
    Looks up a customer ID by name from `support-tickets.json`.

    Args:
        name: The name of the customer to look up.

    Returns:
        A dictionary containing customer details (id, name, email) if found,
        otherwise an empty dictionary.
    """
    data = _load_support_data()
    for customer in data.get("customers", []):
        if customer["name"].lower() == name.lower():
            return customer
    return {}

def lookup_order(customer_id: str, item_keyword: str = None) -> list:
    """
    Retrieves orders for a customer from `support-tickets.json`.

    Args:
        customer_id: The ID of the customer.
        item_keyword: An optional keyword to filter orders by item description.

    Returns:
        A list of dictionaries, where each dictionary represents an order.
    """
    data = _load_support_data()
    customer_orders = []
    for order in data.get("orders", []):
        if order["customer_id"] == customer_id:
            if item_keyword is None or item_keyword.lower() in order["item"].lower():
                customer_orders.append(order)
    return customer_orders

def process_refund(order_id: str) -> str:
    """
    Processes a refund if within policy (returns a success message).

    Args:
        order_id: The ID of the order to process a refund for.

    Returns:
        A string indicating the success or failure of the refund process,
        including details if applicable.
    """
    data = _load_support_data()
    for order in data.get("orders", []):
        if order["order_id"] == order_id:
            order_date_str = order.get("order_date")
            refund_policy = order.get("refund_policy")

            if not order_date_str or not refund_policy:
                return f"Refund cannot be processed for order {order_id}: Missing order date or refund policy."

            try:
                order_date = datetime.datetime.strptime(order_date_str, '%Y-%m-%d').date()
                current_date = datetime.date.today()

                match = re.search(r'(\d+)-day window', refund_policy)
                if match:
                    days_allowed = int(match.group(1))
                    if (current_date - order_date).days <= days_allowed:
                        return f"Refund successfully processed for order {order_id} ({order['item']}). Funds will be returned within 3-5 business days."
                    else:
                        return f"Refund for order {order_id} ({order['item']}) is outside the {days_allowed}-day refund window. Order placed on {order_date_str}."
                else:
                    return f"Refund policy '{refund_policy}' for order {order_id} is not in a recognized 'X-day window' format. Cannot determine eligibility."

            except ValueError:
                return f"Refund cannot be processed for order {order_id}: Invalid order date format '{order_date_str}'."

    return f"Order {order_id} not found."

def escalate_to_human(customer_id: str, issue_summary: str, recommended_action: str) -> str:
    """
    Escalates the ticket to a human agent.

    Args:
        customer_id: The ID of the customer involved.
        issue_summary: A brief summary of the issue.
        recommended_action: The recommended next step for the human agent.

    Returns:
        A summary message indicating the ticket has been escalated.
    """
    # In a real system, this would involve creating an actual ticket in a CRM
    # For this mock, we just return a confirmation message.
    return (
        f"Ticket for customer {customer_id} escalated to a human agent.\n"
        f"Summary: {issue_summary}\n"
        f"Recommended Action: {recommended_action}\n"
        "A human agent will review the case shortly."
    )

# --- Agentic Loop ---
def run_agent():
    """Runs the customer support agent loop."""
    # Client setup
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    # Chat session setup
    chat = client.chats.create(
        model="gemini-3.1-flash-lite",
        config=types.GenerateContentConfig(
            tools=[get_customer, lookup_order, process_refund, escalate_to_human],
            system_instruction="Act as a helpful customer support agent for our company. Verify the customer's identity first."
        )
    )

    print("Welcome to customer support. How can I assist you today?")

    # Loop
    while True:
        user_input = input("You: ")
        if user_input.lower() in ['exit', 'quit']:
            print("Thank you for contacting customer support. Goodbye!")
            break
        response = chat.send_message(user_input)
        print("Agent:", response.text)


if __name__ == "__main__":
    # Ensure GEMINI_API_KEY is set in your environment
    if not os.getenv("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable is not set.")
        print("Please set the GEMINI_API_KEY to your Gemini API key before running.")
    else:
        run_agent()
