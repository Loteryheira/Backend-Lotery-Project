from flask import Flask
from flask_cors import CORS
from src.chat.api_integration import chatbot_api
from dotenv import load_dotenv
import os
import logging
import schedule
import time
import threading

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

# Importa tu función existente para extraer referencias de correos
from src.chat.api_integration import extract_reference_from_email

def check_emails():
    # Llama a tu función para extraer referencias de correos
    reference, amount = extract_reference_from_email()
    if reference and amount:
        # Aquí puedes agregar el código para procesar la referencia y el monto
        app.logger.info(f"Nueva referencia encontrada: {reference}, Monto: {amount}")

# Configura el cron job para ejecutarse cada minuto
schedule.every(1).minute.do(check_emails)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

# Inicia el scheduler en un hilo separado
scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
