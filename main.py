import json
import time
import os
import anthropic
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
API_KEY = os.environ.get("ANTHROPIC_API_KEY")

client = anthropic.Anthropic(api_key=API_KEY)
MODEL_NAME = "claude-haiku-4-5"

# Global state for Identity Gate
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
        "description": "Verifies customer identity by customer ID. Must be called first before accessing orders or processing refunds. Returns customer name if verified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "The customer ID provided by the user (e.g., C123)."}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "lookup_order",
        "description": "Look up a single order by its exact Order ID. Returns order details including item, status, amount, and refund policy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID to look up (e.g., ORD-001)"}
            },
            "required": ["order_id"]
        }
    },
    {
        "name": "get_orders",
        "description": "Retrieve all orders for a verified customer. Use this when customer mentions multiple similar items (e.g., 'water heater') so you can ask them to clarify which one they mean.",
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
        "description": "Process a refund for a verified customer's order. Only call after identity has been verified via get_customer. Amounts over \$1000 will automatically escalate to human review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The exact order ID"},
                "amount": {"type": "number", "description": "The amount to refund in dollars"}
            },
            "required": ["order_id", "amount"]
        }
    },
    {
        "name": "escalate_to_human",
        "description": "Escalate complex issues, high-value refunds, or ambiguous cases to a human manager. Include order ID and amount for context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_summary": {"type": "string", "description": "Short summary of the issue requiring human review"},
                "order_id": {"type": "string", "description": "The order ID being discussed"},
                "amount": {"type": "number", "description": "The amount involved in the issue"}
            },
            "required": ["issue_summary", "order_id", "amount"]
        }
    }
]

def get_customer(customer_id):
    """Verify customer identity. Sets session state."""
    if customer_id in db["customers"]:
        session_state["is_verified"] = True
        session_state["customer_id"] = customer_id
        customer_name = db["customers"][customer_id]["name"]
        return json.dumps({
            "status": "verified",
            "customer_id": customer_id,
            "name": customer_name,
            "message": f"Customer {customer_id} ({customer_name}) verified successfully."
        })
    return json.dumps({
        "status": "error",
        "error_code": "customer_not_found",
        "message": "Error: Customer not found in database."
    })

def lookup_order(order_id):
    """Look up a single order by order_id. Requires identity verification."""
    for cust_id, cust_data in db["customers"].items():
        if order_id in cust_data.get("orders", {}):
            order_details = cust_data["orders"][order_id].copy()
            return json.dumps({
                "status": "found",
                "order_id": order_id,
                "customer_id": cust_id,
                "order": order_details
            })
    return json.dumps({
        "status": "error",
        "error_code": "order_not_found",
        "message": f"Error: Order {order_id} not found in database."
    })

def get_orders(customer_id):
    """Get all orders for a customer. Requires identity verification."""
    if customer_id in db["customers"]:
        orders = db["customers"][customer_id].get("orders", {})
        if not orders:
            return json.dumps({
                "status": "no_orders",
                "customer_id": customer_id,
                "message": "No orders found for this customer."
            })
        return json.dumps({
            "status": "found",
            "customer_id": customer_id,
            "orders": orders
        })
    return json.dumps({
        "status": "error",
        "error_code": "customer_not_found",
        "message": f"Error: Customer {customer_id} not found."
    })

def escalate_to_human(issue_summary, order_id, amount):
    """Escalate to human manager with full context."""
    summary = {
        "status": "escalated",
        "customer_id": session_state.get("customer_id", "Unknown"),
        "order_id": order_id,
        "amount": amount,
        "issue_summary": issue_summary,
        "recommended_action": "Manual review required by manager",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    return json.dumps(summary)

def process_refund(order_id, amount):
    """
    Process a refund. 
    - Amounts > \$1000 automatically escalate.
    - Simulates transient errors for testing retry logic.
    """
    # Check refund limit
    if float(amount) > 1000:
        escalation_result = escalate_to_human(
            f"Refund amount ${amount} exceeds auto-approve limit of \$1000.",
            order_id,
            amount
        )
        return escalation_result

    global refund_attempts
    if order_id not in refund_attempts:
        refund_attempts[order_id] = 0

    max_retries = 1
    for attempt in range(max_retries + 1):
        if refund_attempts[order_id] == 0:
            # First attempt: simulate transient error
            refund_attempts[order_id] += 1
            api_response = {
                "error": "transient",
                "isRetryable": True,
                "message": "Payment gateway timeout (temporary error)"
            }
        else:
            # Second attempt: success
            api_response = {
                "status": "success",
                "order_id": order_id,
                "amount": amount,
                "message": f"Refund of ${amount} processed successfully for order {order_id}."
            }

        if api_response.get("error") == "transient" and api_response.get("isRetryable"):
            print(f"[System: Transient error detected on attempt {attempt + 1}. Retrying...]")
            time.sleep(1)
            continue
        
        return json.dumps(api_response)
    
    return json.dumps({
        "error": "max_retries_exceeded",
        "message": "Error: Refund failed after maximum retry attempts."
    })

def post_tool_use_hook(tool_name, raw_result):
    """
    Comprehensive PostToolUse hook that:
    - Enforces identity gate
    - Logs all tool results
    - Detects transient errors and retry signals
    - Detects validation errors
    - Flags critical escalations
    - Modifies results when necessary
    """
    result_str = str(raw_result)
    
    # ============================================
    # 1. IDENTITY GATE ENFORCEMENT
    # ============================================
    if "ERROR_IDENTITY_NOT_VERIFIED" in result_str:
        print("[🔒 Hook: SECURITY] ⚠️  Blocked unauthorized tool access before identity verified.")
        return "Action blocked by system. You must call get_customer first to verify your identity."
    
    # ============================================
    # 2. TRY TO PARSE RESULT AS JSON
    # ============================================
    try:
        parsed = json.loads(result_str)
    except:
        print(f"[📝 Hook: LOG] Tool '{tool_name}' returned non-JSON result: {result_str[:60]}...")
        return result_str
    
    # ============================================
    # 3. TRANSIENT ERROR HANDLING (Retry Logic)
    # ============================================
    if isinstance(parsed, dict) and parsed.get("error") == "transient":
        is_retryable = parsed.get("isRetryable", False)
        error_msg = parsed.get("message", "Unknown transient error")
        print(f"[⚠️  Hook: TRANSIENT ERROR] Tool '{tool_name}' failed: {error_msg}")
        print(f"[🔄 Hook: RETRY SIGNAL] isRetryable={is_retryable} — This call should be retried automatically")
        return result_str
    
    # ============================================
    # 4. VALIDATION ERROR HANDLING
    # ============================================
    if isinstance(parsed, dict) and parsed.get("error") == "validation":
        error_msg = parsed.get("message", "Unknown validation error")
        print(f"[❌ Hook: VALIDATION FAILED] Tool '{tool_name}' — {error_msg}")
        return result_str
    
    # ============================================
    # 5. ORDER NOT FOUND HANDLING
    # ============================================
    if isinstance(parsed, dict) and parsed.get("error_code") == "order_not_found":
        print(f"[❌ Hook: ORDER NOT FOUND] Tool '{tool_name}' — Order does not exist in database")
        return result_str
    
    # ============================================
    # 6. CUSTOMER NOT FOUND HANDLING
    # ============================================
    if isinstance(parsed, dict) and parsed.get("error_code") == "customer_not_found":
        print(f"[❌ Hook: CUSTOMER NOT FOUND] Tool '{tool_name}' — Customer verification failed")
        return result_str
    
    # ============================================
    # 7. ESCALATION DETECTION & CRITICAL LOGGING
    # ============================================
    if isinstance(parsed, dict) and parsed.get("status") == "escalated":
        order_id = parsed.get("order_id", "UNKNOWN")
        amount = parsed.get("amount", 0)
        issue = parsed.get("issue_summary", "No summary")
        customer_id = parsed.get("customer_id", "UNKNOWN")
        print(f"[🚨 Hook: CRITICAL ALERT] Issue escalated to human manager:")
        print(f"    Customer: {customer_id}")
        print(f"    Order: {order_id}")
        print(f"    Amount: ${amount}")
        print(f"    Issue: {issue}")
        return result_str
    
    # ============================================
    # 8. SUCCESSFUL REFUND LOGGING
    # ============================================
    if tool_name == "process_refund" and isinstance(parsed, dict):
        if parsed.get("status") == "success":
            order_id = parsed.get("order_id", "UNKNOWN")
            amount = parsed.get("amount", 0)
            print(f"[💰 Hook: REFUND APPROVED] Refund processed successfully")
            print(f"    Order: {order_id} | Amount: ${amount}")
        return result_str
    
    # ============================================
    # 9. SUCCESSFUL CUSTOMER VERIFICATION
    # ============================================
    if tool_name == "get_customer" and isinstance(parsed, dict):
        if parsed.get("status") == "verified":
            customer_id = parsed.get("customer_id", "UNKNOWN")
            name = parsed.get("name", "Unknown")
            print(f"[✅ Hook: VERIFIED] Customer {customer_id} ({name}) identity confirmed")
        return result_str
    
    # ============================================
    # 10. SUCCESSFUL ORDER LOOKUP
    # ============================================
    if tool_name == "lookup_order" and isinstance(parsed, dict):
        if parsed.get("status") == "found":
            order_id = parsed.get("order_id", "UNKNOWN")
            print(f"[✅ Hook: FOUND] Order {order_id} retrieved successfully")
        return result_str
    
    # ============================================
    # 11. DEFAULT: LOG AND PASS THROUGH
    # ============================================
    print(f"[📝 Hook: LOG] Tool '{tool_name}' executed successfully")
    return result_str

def execute_tool(tool_name, tool_args):
    """
    Execute a tool with strict identity gate enforcement.
    Only get_customer can run before verification.
    All other tools require is_verified=True.
    """
    # ============================================
    # STRICT IDENTITY GATE: Block unverified access
    # ============================================
    if tool_name != "get_customer" and not session_state["is_verified"]:
        return "ERROR_IDENTITY_NOT_VERIFIED"

    # ============================================
    # DISPATCH TO CORRECT TOOL
    # ============================================
    if tool_name == "get_customer":
        return get_customer(tool_args.get("customer_id"))
    
    elif tool_name == "lookup_order":
        return lookup_order(tool_args.get("order_id"))
    
    elif tool_name == "get_orders":
        return get_orders(tool_args.get("customer_id"))
    
    elif tool_name == "process_refund":
        return process_refund(tool_args.get("order_id"), tool_args.get("amount"))
    
    elif tool_name == "escalate_to_human":
        return escalate_to_human(
            tool_args.get("issue_summary"),
            tool_args.get("order_id"),
            tool_args.get("amount")
        )
    
    return json.dumps({
        "error": "unknown_tool",
        "message": f"Error: Unknown tool '{tool_name}'."
    })

def run_agent():
    """Main agent loop: continuous conversation with tool use."""
    print("=" * 60)
    print("Welcome to Customer Support Agent")
    print("=" * 60)
    print("\nHow can I assist you today? (Type 'exit' or 'quit' to end)\n")
    
    messages = []
    
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ['exit', 'quit']:
            print("\nThank you for using Customer Support. Goodbye!")
            break
        
        if not user_input:
            continue
            
        messages.append({"role": "user", "content": user_input})
        
        # ============================================
        # AGENTIC LOOP
        # ============================================
        while True:
            response = client.messages.create(
                model=MODEL_NAME,
                max_tokens=1000,
                tools=tools,
                messages=messages
            )
            
            # ============================================
            # END TURN: Return final response
            # ============================================
            if response.stop_reason == "end_turn":
                assistant_msg = next(
                    (block.text for block in response.content if hasattr(block, 'text')),
                    ""
                )
                if assistant_msg:
                    print(f"\nAgent: {assistant_msg}\n")
                    messages.append({"role": "assistant", "content": assistant_msg})
                break
            
            # ============================================
            # TOOL USE: Execute requested tools
            # ============================================
            tool_calls_content = []
            tool_results_content = []
            
            for block in response.content:
                # Text response (thinking or explanation)
                if block.type == "text":
                    tool_calls_content.append({"type": "text", "text": block.text})
                    if block.text.strip():
                        print(f"\nAgent: {block.text}")
                
                # Tool use request
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
                    
                    print(f"\n[System: Executing tool: {tool_name}]")
                    
                    # Execute tool
                    raw_result = execute_tool(tool_name, tool_args)
                    
                    # Post-tool-use hook: inspect and log
                    final_result = post_tool_use_hook(tool_name, raw_result)
                    
                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": final_result
                    })
            
            # ============================================
            # APPEND TO MESSAGE HISTORY
            # ============================================
            messages.append({"role": "assistant", "content": tool_calls_content})
            messages.append({"role": "user", "content": tool_results_content})

if __name__ == "__main__":
    run_agent()
