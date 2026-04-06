"""test_interlink.py — verify Interlink messaging and registry."""

import asyncio
import os
import sys
import uuid

# Add current dir to path
sys.path.append(os.getcwd())

from tools import interlink
from memory import store_core

async def test_interlink():
    print("--- [Interlink Test] ---")
    
    # Initialize the pool
    await store_core.open_pool()
    try:
        # 1. Heartbeat
        print("1. Sending Heartbeat...")
        ok = await interlink.heartbeat(
            capabilities=["test_agent", "graph_traversal"],
            current_goal="Verifying Interlink Connectivity"
        )
        if not ok:
            print("FAILED: Heartbeat failed. Is Postgres running?")
            return
        print("SUCCESS: Heartbeat sent.")

        # 2. Registry Check
        print("\n2. Checking Active Agents...")
        agents = await interlink.get_active_agents(max_age_seconds=120)
        found_me = any(a['agent_id'] == interlink.AGENT_ID for a in agents)
        if found_me:
            print(f"SUCCESS: Found myself ({interlink.AGENT_ID}) in the registry.")
        else:
            print(f"FAILED: Could not find ({interlink.AGENT_ID}) in the registry.")
            print(f"Agents found: {[a['agent_id'] for a in agents]}")

        # 3. Message Send
        print("\n3. Sending Test Message (Self-Directed)...")
        msg_content = f"Hello from the test suite! {uuid.uuid4()}"
        msg_id = await interlink.send_message(
            receiver_id=interlink.AGENT_ID,
            content=msg_content,
            subject="Test Submission",
            interaction_type="query"
        )
        if msg_id:
            print(f"SUCCESS: Message sent with ID: {msg_id}")
        else:
            print("FAILED: Message send failed.")
            return

        # 4. Message Poll
        print("\n4. Polling for Unread Messages...")
        msgs = await interlink.list_messages(status="unread")
        my_msg = next((m for m in msgs if m['id'] == msg_id), None)
        if my_msg and my_msg['content'] == msg_content:
            print(f"SUCCESS: Retrieved test message correctly.")
        else:
            print("FAILED: Message not found in mailbox.")

        # 5. Mark Read
        print("\n5. Marking Message as Read...")
        ok = await interlink.mark_message_status(msg_id, status="read")
        if ok:
            print(f"SUCCESS: Message {msg_id} marked as read.")
        else:
            print("FAILED: Could not update status.")

        # 6. Verify Read status
        print("\n6. Verifying Poll excluding read messages...")
        msgs_after = await interlink.list_messages(status="unread")
        if not any(m['id'] == msg_id for m in msgs_after):
            print("SUCCESS: Message is no longer in unread queue.")
        else:
            print("FAILED: Message still appearing as unread.")

        # 7. Cleanup
        print("\n7. Running Cleanup...")
        count = await interlink.cleanup_old_messages()
        print(f"Cleanup finished. (Deleted {count} old messages based on TTL).")

    finally:
        await store_core.close_pool()

    print("\n--- [Test Complete] ---")

if __name__ == "__main__":
    asyncio.run(test_interlink())
