from flask import Flask
from flask_cors import CORS
from src.chat.api_integration import chatbot_api
from dotenv import load_dotenv
import os

app = Flask(__name__)

load_dotenv()

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')


app = Flask(__name__)
CORS(app)

app.register_blueprint(chatbot_api)

if __name__ == "__main__":
    app.run(debug=True , port=5000, host="0.0.0.0")