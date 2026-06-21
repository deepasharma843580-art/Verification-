import threading
from flask import Flask
from verifier import run_loop

app = Flask(__name__)


@app.route("/")
def home():
    return "✅ FamApp Verifier Bot is running."


def start_background_loop():
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()


start_background_loop()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
  
