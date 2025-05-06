from flask import Flask
from flask_cors import CORS
from src.chat.api_integration import chatbot_api, extraer_mensajes_gmail
from dotenv import load_dotenv
import os
import logging
import schedule
import time
import threading

app = Flask(__name__)

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
    encoding='utf-8'  # Asegúrate de usar UTF-8
)

@app.route('/')
def index():
    return "¡Hola, mundo!"

# Reducir el nivel de logs para PyMongo
logging.getLogger("pymongo").setLevel(logging.WARNING)

# Función para buscar correos cada minuto
def check_emails():
    try:
        app.logger.info("[DEBUG] Iniciando proceso de búsqueda de correos...")
        remitente = "adrianrincon102001@gmail.com"
        result = extraer_mensajes_gmail(remitente)
        if result:
            referencia, monto = result
            app.logger.info(f"Correo encontrado: Referencia {referencia}, Monto {monto}")
        else:
            app.logger.info("No hay mensajes nuevos sin leer.")
    except Exception as e:
        app.logger.error(f"Error en el cron job de búsqueda de correos: {str(e)}")

# Configurar el cron job para ejecutarse cada 30 segundos
schedule.every(30).seconds.do(check_emails)

def run_scheduler():
    with app.app_context():
        while True:
            schedule.run_pending()
            time.sleep(1)

# Inicia el scheduler en un hilo separado
scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)