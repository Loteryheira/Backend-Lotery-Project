from flask import Flask
from flask_cors import CORS
from src.chat.api_integration import chatbot_api
from dotenv import load_dotenv
import os
import logging

app = Flask(__name__, static_folder="/src/static")

load_dotenv()

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

CORS(app)

app.register_blueprint(chatbot_api)

# Configurar el nivel de logging
app.logger.setLevel(logging.DEBUG)

# Configurar el logger principal con formato y fecha
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Reducir el nivel de logs para PyMongo
logging.getLogger("pymongo").setLevel(logging.WARNING)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)