import json
import time
import anthropic

API_KEY = "sk-ant-api03-xxxxxxxxxxxxxx"
client = anthropic.Anthropic(api_key=API_KEY)
MODEL_NAME = "claude-3-haiku-20240307"

session_state = {
    "is_verified": False,
    "customer_id": None
}

refund_attempts = {}

def load_db():
    with open("support-tickets.json", "r") as f:
        return json.load(f)

db = load_db()

tools = [
    {
        "name": "get_customer",
        "description": "Verifies customer identity. Must be called first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "The customer ID provided by the user."}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "lookup_order",
        "description": "Look up an order by customer ID or Order ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID to look up (e.g., O1001)"}
            },
            "required": ["order_id"]
        }
    },
    {
        "name": "get_orders",
        "description": "Get a list of all orders for a specific customer ID so Claude can ask the user which order they mean.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "The exact customer ID"}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "process_refund",
        "description": "Process a refund for a given order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The exact order ID"},
                "amount": {"type": "number", "description": "The amount to refund"}
            },
            "required": ["order_id", "amount"]
        }
    },
    {
        "name": "escalate_to_human",
        "description": "Escalate complex issues or high-value refunds to a human manager.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_summary": {"type": "string", "description": "Short summary of the issue"}
            },
            "required": ["issue_summary"]
        }
    }
]

def get_customer(customer_id):
    if customer_id in db["customers"]:
        session_state["is_verified"] = True
        session_state["customer_id"] = customer_id
        return f"Success: Customer {customer_id} verified. Name: {db['customers'][customer_id]['name']}"
    return "Error: Customer not found."

def lookup_order(order_id):
    for cust_id, cust_data in db["customers"].items():
        if order_id in cust_data.get("orders", {}):
            return json.dumps(cust_data["orders"][order_id])
    return "Error: Order not found."

def get_orders(customer_id):
    if customer_id in db["customers"]:
        orders = db["customers"][customer_id].get("orders", {})
        if not orders:
            return "No orders found for this customer."
        return json.dumps(orders)
    return "Error: Customer not found."

def escalate_to_human(issue_summary):
    summary = {
        "status": "escalated",
        "customer_id": session_state.get("customer_id", "Unknown"),
        "issue_summary": issue_summary,
        "recommended_action": "Manual review required by manager",
        "timestamp": time.time()
    }
    return json.dumps(summary)

def process_refund(order_id, amount):
    global refund_attempts

    if not session_state["is_verified"]:
        return "ERROR_IDENTITY_NOT_VERIFIED: You must call get_customer to verify identity first."

    if float(amount) > 1000:
        return escalate_to_human(f"Refund amount ${amount} exceeds the $1,000 auto-approve limit for order {order_id}.")

    if order_id not in refund_attempts:
        refund_attempts[order_id] = 0

    max_retries = 1
    for attempt in range(max_retries + 1):
        if refund_attempts[order_id] == 0:
            refund_attempts[order_id] += 1
            api_response = {"error": "transient", "isRetryable": True, "message": "Payment gateway timeout"}
        else:
            api_response = {"status": "success", "message": f"Refund of ${amount} processed for order {order_id}."}

        if api_response.get("error") == "transient" and api_response.get("isRetryable"):
            print(f"[System: Transient error detected on attempt {attempt + 1}. Retrying...]")
            time.sleep(1)
            continue
        
        return json.dumps(api_response)
        
    return "Error: Refund failed after max retries."

def post_tool_use_hook(tool_name, raw_result):
    result_str = str(raw_result)
    if "ERROR_IDENTITY_NOT_VERIFIED" in result_str:
        return "Action blocked by system. Tell the user you must verify their ID first."
    return result_str

def execute_tool(tool_name, tool_args):
    if tool_name == "get_customer":
        return get_customer(tool_args["customer_id"])
    elif tool_name == "lookup_order":
        return lookup_order(tool_args["order_id"])
    elif tool_name == "get_orders":
        return get_orders(tool_args["customer_id"])
    elif tool_name == "process_refund":
        return process_refund(tool_args["order_id"], tool_args["amount"])
    elif tool_name == "escalate_to_human":
        return escalate_to_human(tool_args["issue_summary"])
    return "Error: Unknown tool."

def run_agent():
    print("Welcome to customer support. How can I assist you today?")
    messages = []
    
    while True:
        user_input = input("\nYou: ")
        if user_input.lower() in ['exit', 'quit']:
            break
            
        messages.append({"role": "user", "content": user_input})
        
        while True:
            response = client.messages.create(
                model=MODEL_NAME,
                max_tokens=1000,
                tools=tools,
                messages=messages
            )
            
            if response.stop_reason != "tool_use":
                assistant_msg = next((block.text for block in response.content if hasattr(block, 'text')), "")
                print(f"\nAgent: {assistant_msg}")
                messages.append({"role": "assistant", "content": assistant_msg})
                break
                
            tool_calls_content = []
            tool_results_content = []
            
            for block in response.content:
                if block.type == "text":
                    tool_calls_content.append({"type": "text", "text": block.text})
                    print(f"\nAgent: {block.text}")
                elif block.type == "tool_use":
                    tool_name = block.name
                    tool_args = block.input
                    tool_id = block.id
                    
                    tool_calls_content.append({
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tool_name,
                        "input": tool_args
                    })
                    
                    print(f"[System executing tool: {tool_name}]")
                    
                    raw_result = execute_tool(tool_name, tool_args)
                    final_result = post_tool_use_hook(tool_name, raw_result)
                    
                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": final_result
                    })

            messages.append({"role": "assistant", "content": tool_calls_content})
            messages.append({"role": "user", "content": tool_results_content})

if __name__ == "__main__":
    run_agent()
