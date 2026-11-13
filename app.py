import os
import re
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# Track active and recent call states with history
# Structure: { call_id: { "call_id": ..., "phase": 1, "captured_digits": [], "transcript": ..., "updated_at": ... } }
call_states = {}

ENCODING_TABLE = {
    "0": "Zuri", "1": "Uni", "2": "Tuwi", "3": "Thuri", "4": "Furi",
    "5": "Fivi", "6": "Siji", "7": "Sivini", "8": "Eigti", "9": "Nini"
}

WORD_TO_DIGIT = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"
}

def extract_all_digits(text: str):
    """Extracts all numeric digits and number words from text in exact order of appearance."""
    text_lower = text.lower()
    pattern = r'\b(zero|one|two|three|four|five|six|seven|eight|nine|[0-9])\b'
    matches = re.findall(pattern, text_lower)
    
    digits = []
    for m in matches:
        if m.isdigit():
            digits.append(m)
        elif m in WORD_TO_DIGIT:
            digits.append(WORD_TO_DIGIT[m])
    return digits

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        payload = request.json or {}
        data = payload.get("data", payload)
        
        call_id = data.get("call_id") or data.get("id")
        transcript = data.get("transcript", data.get("text", ""))
        
        if not call_id:
            return jsonify({"status": "ignored"}), 200

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        # Initialize state for new calls
        if call_id not in call_states:
            call_states[call_id] = {
                "call_id": call_id,
                "phase": 1,
                "captured_digits": [],
                "last_processed_text": "",
                "transcript": transcript,
                "status": "In Progress",
                "updated_at": timestamp
            }

        state = call_states[call_id]
        state["transcript"] = transcript
        state["updated_at"] = timestamp

        # Prevent re-processing identical transcript updates
        if transcript == state["last_processed_text"]:
            return jsonify({"status": "no_change"}), 200
        
        state["last_processed_text"] = transcript
        all_found_digits = extract_all_digits(transcript)

        # -------------------------------------------------------------
        # PHASE 1: DETECT AND PRESS THE FIRST DIGIT
        # -------------------------------------------------------------
        if state["phase"] == 1:
            if all_found_digits:
                first_digit = all_found_digits[0]
                state["phase"] = 2  # Transition to collecting the next 6 digits
                
                # Queue any extra digits spoken in the same initial sentence
                remaining_digits = all_found_digits[1:]
                if remaining_digits:
                    state["captured_digits"].extend(remaining_digits)

                # Instruct platform to press the first digit; keep speech silent (text: "")
                return jsonify({
                    "action": "press_digit",
                    "digit": first_digit,
                    "text": ""
                }), 200
            
            return jsonify({"text": ""}), 200

        # -------------------------------------------------------------
        # PHASE 2 & 3: CAPTURE NEXT 6 DIGITS & ENCODE
        # -------------------------------------------------------------
        elif state["phase"] == 2:
            current_count = len(state["captured_digits"])
            needed = 6 - current_count
            
            if needed > 0 and all_found_digits:
                new_digits = all_found_digits[current_count:]
                if new_digits:
                    state["captured_digits"].extend(new_digits[:needed])

            # Check if we have successfully accumulated exactly 6 digits
            if len(state["captured_digits"]) >= 6:
                six_digits = state["captured_digits"][:6]
                state["phase"] = 3
                state["status"] = "Completed & Hung Up"
                
                # Map digits to custom encoded words
                encoded_words = [ENCODING_TABLE[d] for d in six_digits if d in ENCODING_TABLE]
                response_text = " ".join(encoded_words)
                state["final_response"] = response_text
                
                # Output exact encoded words and command the call to end
                return jsonify({
                    "text": response_text,
                    "end_call": True
                }), 200
            else:
                return jsonify({"text": ""}), 200

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"Webhook Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- URL LOG VIEWER ENDPOINTS ---

@app.route("/logs", methods=["GET"])
def view_logs():
    """Renders a clean, auto-refreshing HTML table of call logs directly in the browser."""
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>AgentPhone Webhook Logs</title>
        <meta http-equiv="refresh" content="5">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; }
            h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
            p { color: #94a3b8; font-size: 0.875rem; margin-bottom: 1.5rem; }
            table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 8px; overflow: hidden; }
            th, td { padding: 0.75rem 1rem; text-align: left; border-bottom: 1px solid #334155; font-size: 0.9rem; }
            th { background: #334155; color: #e2e8f0; font-weight: 600; }
            tr:hover { background: #273548; }
            .badge-completed { background: #065f46; color: #34d399; padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.75rem; }
            .badge-progress { background: #1e3a8a; color: #60a5fa; padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.75rem; }
        </style>
    </head>
    <body>
        <h1>Live Webhook Call Logs</h1>
        <p>Auto-refreshes every 5 seconds. Showing active and recent call states.</p>
        <table>
            <thead>
                <tr>
                    <th>Call ID</th>
                    <th>Status / Phase</th>
                    <th>Captured Digits</th>
                    <th>Latest Transcript</th>
                    <th>Final Response Spoken</th>
                    <th>Last Updated</th>
                </tr>
            </thead>
            <tbody>
                {% if calls %}
                    {% for cid, data in calls.items() %}
                    <tr>
                        <td><code>{{ cid }}</code></td>
                        <td>
                            {% if data.phase == 3 %}
                                <span class="badge-completed">{{ data.status }}</span>
                            {% else %}
                                <span class="badge-progress">Phase {{ data.phase }} (In Progress)</span>
                            {% endif %}
                        </td>
                        <td><strong>{{ "".join(data.captured_digits) }}</strong></td>
                        <td>{{ data.transcript }}</td>
                        <td><span style="color: #38bdf8;">{{ data.get('final_response', '-') }}</span></td>
                        <td>{{ data.updated_at }}</td>
                    </tr>
                    {% endfor %}
                {% else %}
                    <tr>
                        <td colspan="6" style="text-align: center; color: #64748b; padding: 2rem;">No calls recorded yet. Place a test call to see logs here.</td>
                    </tr>
                {% endif %}
            </tbody>
        </table>
    </body>
    </html>
    """
    return render_template_string(html_template, calls=call_states)

@app.route("/logs/json", methods=["GET"])
def view_logs_json():
    """Returns raw JSON logs for external script fetching or debugging."""
    return jsonify(call_states), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
          
