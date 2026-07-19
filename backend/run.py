"""Development entrypoint: ``python run.py``."""
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("MYMEAL_PORT", "7850"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("MYMEAL_DEBUG") == "1")
