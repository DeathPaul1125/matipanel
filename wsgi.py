"""WSGI entry point para Gunicorn/uwsgi."""
from app import create_app

application = create_app()
app = application  # alias para flask run

if __name__ == "__main__":
    # Desarrollo local: HTTP en localhost:8080
    app.run(host="127.0.0.1", port=8080, debug=True)
