# chat_ollama.py
import json
import requests

OLLAMA_URL = "http://100.108.141.26:11434/api/chat"
MODEL = "qwen35-fast:latest"

messages = [
    {
        "role": "system",
        "content": "你是一個直接、清楚、精煉的助理。回答要簡潔，不要輸出思考過程。",
    }
]

def chat(user_input: str):
    messages.append({"role": "user", "content": user_input})

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "options": {
            "num_ctx": 2048,
            "num_predict": 128,
            "temperature": 0.7,
        },
    }

    reply = ""

    with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=600) as r:
        r.raise_for_status()

        print("AI> ", end="", flush=True)

        for line in r.iter_lines():
            if not line:
                continue

            data = json.loads(line.decode("utf-8"))

            msg = data.get("message", {})
            content = msg.get("content", "")

            if content:
                print(content, end="", flush=True)
                reply += content

            if data.get("done"):
                break

    print()
    messages.append({"role": "assistant", "content": reply})


def main():
    print("Ollama remote chat started.")
    print("Type /exit to quit, /clear to clear memory.\n")

    while True:
        try:
            user_input = input("You> ").strip()
        except KeyboardInterrupt:
            print("\nBye.")
            break

        if not user_input:
            continue

        if user_input in {"/exit", "/quit", "exit", "quit"}:
            print("Bye.")
            break

        if user_input == "/clear":
            messages[:] = [
                {
                    "role": "system",
                    "content": "你是一個直接、清楚、精煉的助理。回答要簡潔，不要輸出思考過程。",
                }
            ]
            print("Memory cleared.\n")
            continue

        try:
            chat(user_input)
        except requests.exceptions.HTTPError as e:
            body = e.response.text.strip() if e.response is not None else ""
            detail = f"\n{body}" if body else ""
            print(f"\nRequest error: {e}{detail}")
        except requests.exceptions.RequestException as e:
            print(f"\nRequest error: {e}")
        except json.JSONDecodeError as e:
            print(f"\nJSON decode error: {e}")


if __name__ == "__main__":
    main()
