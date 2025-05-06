from flask import Blueprint, request, jsonify, current_app as app
from src.database.db import friends_collection, chat_sessions_collection, sales_collection, comprobantes_collection
from datetime import datetime
import openai
import random
import re
from PIL import Image
import re 
import requests
import time
from io import BytesIO
from google import genai
from google.genai import types
from src.chat.correo_verificacion import extraer_mensajes_gmail  
from dotenv import load_dotenv, find_dotenv
import os
import base64
import imghdr

load_dotenv()

chatbot_api = Blueprint("chatbot_api", __name__)

client = openai.Client()
openai.api_key = os.getenv("OPENAI_API_KEY")

ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

#------------------------- Función para generar respuesta de IA --------------------------

def generate_ai_response(ia_info, user_name, prompt, is_greeting, phone_number, audio_url):
    ia_name = ia_info["name"]
    ia_description = ia_info["description"] if ia_info else "Soy un asistente virtual."

    training_content = ia_info.get('training_content', "")
    if training_content:
        introduction = f"Hi {user_name}, i'am {ia_name}, {ia_description} I am trained with the following information: {training_content[:500]}..."
    else:
        introduction = f"Hi {user_name}, i'am {ia_name}, {ia_description}"

    chat_session = chat_sessions_collection.find_one({"phone_number": phone_number, "ia_name": ia_name})
    if not chat_session:
        chat_session = {
            "phone_number": phone_number,
            "chat_history": [{"role": "system", "content": introduction}],
            "ia_name": ia_name,
            "favorite": False,
            "user_name": user_name
        }
        chat_session_id = chat_sessions_collection.insert_one(chat_session).inserted_id
        chat_session = chat_sessions_collection.find_one({"_id": chat_session_id})
    else:
        chat_session_id = chat_session["_id"]

    chat_history = chat_session.get('chat_history', [])[-19:]
    messages = [{"role": msg.get('role', 'user'), "content": msg.get('content', '')} for msg in chat_history]
    messages.append({"role": "system", "content": introduction})
    messages.append({"role": "user", "content": prompt})

    prompt_to_gpt = f"{introduction}\n\nUsando esta información, responde a la declaración de {user_name}.\n\nDeclaración: {prompt}"

    messages.append({"role": "user", "content": prompt_to_gpt})

    ai_response = None

    try:
        response = client.chat.completions.create(
            model="gpt-4o-2024-05-13",
            messages=messages,
            temperature=0.6,
        )
        ai_response = "La respuesta generada fue inesperadamente vacía."
        if response.choices and response.choices[0].message.content.strip():
            full_response = response.choices[0].message.content.strip()
            response_parts = full_response.rsplit('. ', 1)
            ai_response = response_parts[0] + '.' if len(response_parts) > 1 else full_response

        lines = ai_response.split('\n')
        for i, line in enumerate(lines):
            if not line.lstrip().startswith('- ') and not line.lstrip().startswith(tuple(f"{n}. " for n in range(1, 11))):
                lines[i] = line.replace('. ', '.\n')
        ai_response = '\n'.join(lines)

    except Exception as e:
        print(f"Error inesperado: {e}")
        ai_response = "Ocurrió un error al llamar a la API de OpenAI."

    chat_sessions_collection.update_one(
        {"_id": chat_session_id},
        {"$push": {"chat_history": {"$each": [{'user_message': prompt, 'ai_response': ai_response, 'audio_url': audio_url, 'timestamp': datetime.now(), 'favorite':False}], "$slice": -20}}}
    )

    return ai_response

#------------------------- Función simplificada para la lógica de chat --------------------------

def download_image_from_url(image_url):
    try:
        if not image_url:
            app.logger.info("URL de la imagen está vacía.")
            return None

        app.logger.info(f"Intentando descargar la imagen desde la URL: {image_url}")
        
        # Descargar la imagen sin autenticación
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()

        # Verificar el tipo de archivo descargado
        file_content = response.content
        file_type = imghdr.what(None, h=file_content)
        if not file_type:
            app.logger.info(f"El contenido descargado no es una imagen válida.")
            return None

        # Verificar si la carpeta 'static' existe, si no, crearla
        static_folder = os.path.join(os.path.dirname(__file__), '..', 'static')
        if not os.path.exists(static_folder):
            os.makedirs(static_folder)
            app.logger.info(f"Carpeta 'static' creada en: {static_folder}")

        # Guardar la imagen con la extensión correcta
        file_name = f"downloaded_image.{file_type}"
        image_path = os.path.join(static_folder, file_name)
        with open(image_path, "wb") as f:
            f.write(file_content)
        app.logger.info(f"Imagen descargada y guardada en: {image_path}")
        return image_path
    except requests.exceptions.RequestException as req_err:
        app.logger.info(f"Error de red al descargar la imagen: {str(req_err)}")
    except Exception as e:
        app.logger.info(f"Error al procesar la imagen: {str(e)}")
    return None

def extract_text_from_image_with_gemini(image_path, api_key):
    try:
        # Leer la imagen desde el archivo local
        with open(image_path, "rb") as image_file:
            image_data = image_file.read()

        # Crear el cliente de Gemini
        client = genai.Client(api_key=api_key)

        # Enviar la solicitud al modelo de Gemini
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=["Extract the reference number and amount from this image",
                      types.Part.from_bytes(data=image_data, mime_type="image/jpeg")]
        )

        # Extraer el texto de la respuesta
        extracted_text = response.text
        app.logger.info(f"Texto extraído: {extracted_text}")
        return extracted_text

    except Exception as e:
        app.logger.info(f"Error al usar Gemini API: {str(e)}")
        return None

#------------------------- Función para extraer el monto de un texto --------------------------

def extract_amount(text):
    # Expresión regular para extraer el monto, permitiendo comas como separadores de miles
    match = re.search(r'\b\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b', text)
    if match:
        amount_str = match.group().replace(",", "")
        return int(float(amount_str))  # Convertir a entero
    return None

#------------------------- Función para enviar mensajes de WhatsApp --------------------------

def send_whatsapp_message(phone_number, message):
    """
    Envía un mensaje de WhatsApp usando la API de WhatsApp Business.
    """
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        app.logger.error("Las credenciales de WhatsApp Business no están configuradas correctamente.")
        app.logger.error(f"ACCESS_TOKEN: {ACCESS_TOKEN}")
        app.logger.error(f"PHONE_NUMBER_ID: {PHONE_NUMBER_ID}")
        return None

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,  # Asegúrate de que el número incluya el prefijo 'whatsapp:'
        "type": "text",
        "text": {
            "body": message
        }
    }

    app.logger.info(f"Payload enviado: {payload}")  # Log del payload

    try:
        response = requests.post(WHATSAPP_URL, headers=headers, json=payload)
        response.raise_for_status()
        app.logger.info(f"Mensaje enviado correctamente a {phone_number}: {message}")
        return response.json()
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Error al enviar mensaje a {phone_number}: {str(e)}")
        if e.response is not None:
            app.logger.error(f"Respuesta de la API: {e.response.text}")  # Log de la respuesta de la API
        return None

#------------------------- Función simplificada para la lógica de chat --------------------------

def chat_logic_simplified(phone_number, prompt, ai_name=None, image_url=None):
    if ai_name is None:
        return "¡Ay mi Dios! Algo salió mal, vuelva a intentarlo más tarde."

    try:
        # Obtener información de la IA
        ia_info = friends_collection.find_one({"name": "Tía Maria"})
        if not ia_info:
            return "¡Upe! La Tía María está ocupada, intente más tarde."

        # Atributos de la IA
        cierres = ia_info.get('cierre_venta', {}).get('frases', [])

        # Buscar o crear una sesión de chat
        chat_session = chat_sessions_collection.find_one(
            {"phone_number": phone_number, "ia_name": "Tía Maria"}
        )
        if not chat_session:
            chat_session = {
                "phone_number": phone_number,
                "ia_name": "Tía Maria",
                "chat_history": [],
                "etapa_venta": "inicio",
                "numeros": [],
                "monto": 0,
                "referencia_pago": "",
                "ultima_actualizacion": datetime.now().isoformat(),
                "apuestas": [],
                "procesando_pago": False  # Nuevo campo para indicar si se está procesando un pago
            }
            chat_sessions_collection.insert_one(chat_session)

        etapa_venta = chat_session.get("etapa_venta", "inicio")
        apuestas = chat_session.get("apuestas", [])
        procesando_pago = chat_session.get("procesando_pago", False)

        # Si se está procesando un pago y el usuario envía otro mensaje
        if procesando_pago:
            return "Estamos procesando su comprobante de pago. Por favor, espere unos momentos. 🙏"

        # Etapa: Inicio
        if etapa_venta == "inicio" or "hola" in prompt.lower():
            ai_response = (
                "¡Hola sobrin@! Bienvenido al sistema de tiempos apuntados. "
                "Por favor, indícame los números que deseas apuntar y en qué sorteo (1pm, 4pm, 7pm). "
                "Por ejemplo: 'Quiero apuntar 200 al 8 para las 1pm, 400 al 9 para las 4pm y 150 al 10 para las 7pm'.\n"
                "¡Buena suerte!"
            )
            etapa_venta = "solicitar_numeros"

        # Etapa: Solicitar números
        elif etapa_venta == "solicitar_numeros":
            try:
                apuestas_raw = re.findall(r'(\d+)\s+al\s+(\d{1,2})\s+para\s+las\s+(\d{1,2}(?:am|pm))', prompt)
                if not apuestas_raw:
                    raise ValueError("Formato de apuesta no válido.")

                apuestas_detalle = []
                total_monto = 0

                for monto_str, numero, ronda in apuestas_raw:
                    monto = int(monto_str)
                    total_monto += monto
                    apuestas_detalle.append({"numero": numero, "ronda": ronda.lower(), "monto": monto})

                ai_response = (
                    f"¡Listo! 💵 Apuntando un total de ¢{total_monto:,}.\n"
                    "**Instrucciones de pago:**\n"
                    "1. Transfiere al SINPE MÓVIL: 8888-8888\n"
                    "2. Envíe el NÚMERO DE REFERENCIA de su comprobante o una captura de pantalla\n"
                    "3. Espere la confirmación de su apuntado mientras verificamos su pago (2 min max)\n"
                    "Gracias por confiar en nosotros. ¡Buena suerte en el sorteo! 🍀"
                    f"{random.choice(cierres)} 🍀"
                )
                etapa_venta = "validar_pago"
                apuestas = apuestas_detalle

            except Exception as e:
                app.logger.error(f"Error al procesar las apuestas: {str(e)}")
                ai_response = "¡Upe! 😅 Formato de apuesta inválido. Por favor, intente nuevamente."

        # Etapa: Validar pago
        elif etapa_venta == "validar_pago":
            if image_url:
                # Actualizar el estado de procesamiento de pago
                chat_sessions_collection.update_one(
                    {"_id": chat_session["_id"]},
                    {"$set": {"procesando_pago": True}}
                )

                # Respuesta de la IA indicando que se está procesando el comprobante
                ai_response = "Procesando su comprobante de pago... 🕒"

                # Descargar y procesar la imagen
                image_path = download_image_from_url(image_url)
                if image_path:
                    extracted_text = extract_text_from_image_with_gemini(image_path, os.getenv("GEMINI_API_KEY"))
                    if extracted_text:
                        referencia_match = re.search(r'\b\d{20,30}\b', extracted_text)
                        monto_match = re.search(r'\b\d+[\.,]?\d{2}\b', extracted_text)
                        if referencia_match and monto_match:
                            referencia_pago = referencia_match.group()
                            monto_pago = extract_amount(extracted_text)

                            # Iniciar espera de 2 minutos para verificar el comprobante
                            start_time = datetime.now()
                            while (datetime.now() - start_time).total_seconds() < 120:
                                comprobante = comprobantes_collection.find_one({
                                    "referencia": referencia_pago,
                                    "monto": monto_pago,
                                    "usado": False
                                })
                                if comprobante:
                                    app.logger.info(f"Comprobante encontrado: {comprobante}")
                                    comprobantes_collection.update_one(
                                        {"_id": comprobante["_id"]},
                                        {"$set": {"usado": True}}
                                    )
                                    factura = (
                                        f"🎉 *¡Comprobante Validado!*\n\n"
                                        f"🧾 *Factura de Venta*\n"
                                        f"📅 Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
                                        f"💳 Referencia de Pago: {referencia_pago}\n"
                                        f"💰 Monto Total: ¢{monto_pago:,}\n"
                                        "🔢 Números Apuntados:\n"
                                    )
                                    for apuesta in apuestas:
                                        factura += f"   - Número: {apuesta['numero']} | Ronda: {apuesta['ronda']} | Monto: ¢{apuesta['monto']:,}\n"
                                    factura += "\n🍀 ¡Gracias por confiar en nosotros! ¡Buena suerte en el sorteo!\n⚠️ *Nota:* No realizamos devoluciones. 🙏\n⚠️ *Nota:* Cualquier inconveniente comunicarse al soporte 8888-8888"

                                    # Guardar la factura en la colección `sales`
                                    sales_collection.insert_one({
                                        "phone_number": phone_number,
                                        "referencia_pago": referencia_pago,
                                        "monto": monto_pago,
                                        "apuestas": apuestas,
                                        "fecha": datetime.now().isoformat(),
                                        "factura": factura
                                    })

                                    # Finalizar el estado de procesamiento de pago
                                    chat_sessions_collection.update_one(
                                        {"_id": chat_session["_id"]},
                                        {"$set": {"procesando_pago": False}}
                                    )

                                    return factura
                                time.sleep(10)

                            # Si no se encontró el comprobante dentro del tiempo límite
                            chat_sessions_collection.update_one(
                                {"_id": chat_session["_id"]},
                                {"$set": {"procesando_pago": False}}
                            )
                            return "No se encontró el comprobante en el tiempo límite. Por favor, intente nuevamente."
                        else:
                            chat_sessions_collection.update_one(
                                {"_id": chat_session["_id"]},
                                {"$set": {"procesando_pago": False}}
                            )
                            return "No se encontró referencia o monto en la imagen."
                    else:
                        chat_sessions_collection.update_one(
                            {"_id": chat_session["_id"]},
                            {"$set": {"procesando_pago": False}}
                        )
                        return "No se pudo extraer texto de la imagen."
                else:
                    chat_sessions_collection.update_one(
                        {"_id": chat_session["_id"]},
                        {"$set": {"procesando_pago": False}}
                    )
                    return "No se pudo descargar la imagen."
            else:
                return "No se proporcionó una URL de imagen."

        # Actualizar la sesión de chat
        chat_sessions_collection.update_one(
            {"_id": chat_session["_id"]},
            {
                "$set": {"etapa_venta": etapa_venta, "apuestas": apuestas},
                "$push": {"chat_history": {"user_message": prompt, "ai_response": ai_response, "timestamp": datetime.now()}}
            }
        )

        return ai_response

    except Exception as e:
        app.logger.error(f"Error crítico: {str(e)}")
        return "¡Ay mi Dios! Se me cruzaron los cables. ¿Me repite sobrin@?"

#------------------- API Endpoints -------------------

@chatbot_api.route("/api/v1/amigo", methods=["POST"])
def create_friend():
    try:
        data = request.json
        
        required_fields = ["name", "description", "atributos", "frases_venta", "cierre_venta"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Campo requerido faltante: {field}"}), 400

        friend_data = {
            "name": data["name"],
            "description": data["description"],
            "gender": data.get("gender", "Femenino"),
            "atributos": {
                "personalidad": data["atributos"].get("personalidad", ["amable", "respetuosa"]),
                "estilo_comunicacion": {
                    "saludo": data["atributos"].get("estilo_comunicacion", {}).get("saludo", "¡Buenas buenas! ¿Qué me cuenta?"),
                    "modismos": data["atributos"].get("estilo_comunicacion", {}).get("modismos", ["pura vida", "mae"])
                }
            },
            "frases_venta": data["frases_venta"],
            "cierre_venta": {
                "frases": data["cierre_venta"].get("frases", ["¡Pura vida!"]),
                "accion_final": data["cierre_venta"].get("accion_final", "Despedida con bendición")
            },
            "detalles_extra": {
                "region": data.get("detalles_extra", {}).get("region", "Costa Rica"),
                "referencias_culturales": data.get("detalles_extra", {}).get("referencias_culturales", ["fútbol tico"])
            }
        }

        friends_collection.insert_one(friend_data)
        return jsonify({"message": "IA creada exitosamente."}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chatbot_api.route("/api/v1/chat/whatsapp", methods=["GET", "POST"])
def chat_whatsapp_endpoint():
    try:
        # Manejar la verificación del webhook (GET)
        if request.method == "GET":
            hub_mode = request.args.get("hub.mode")
            hub_challenge = request.args.get("hub.challenge")
            hub_verify_token = request.args.get("hub.verify_token")

            VERIFY_TOKEN = "perroblanco"
            if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
                app.logger.info("Webhook verificado correctamente.")
                return hub_challenge, 200
            else:
                app.logger.warning("Fallo en la verificación del webhook. Token inválido.")
                return "Token de verificación inválido.", 400

        # Manejar los mensajes entrantes (POST)
        elif request.method == "POST":
            # Leer los datos enviados por WhatsApp Business API
            incoming_msg = request.form.get("Body", "").strip()
            sender_phone_number = request.form.get("From", "").strip()
            media_url = request.form.get("MediaUrl0", "").strip()

            app.logger.info(f"Mensaje recibido: {incoming_msg}")
            app.logger.info(f"Número del remitente: {sender_phone_number}")
            app.logger.info(f"URL de la imagen recibida: {media_url}")

            if not incoming_msg and not media_url:
                app.logger.info("El mensaje recibido está vacío y no contiene una URL de imagen.")
                return "No se recibió ningún mensaje ni imagen.", 400

            # Llamar a la lógica del chat
            ai_response = chat_logic_simplified(
                sender_phone_number, incoming_msg, ai_name="Tía Maria", image_url=media_url
            )

            # Enviar la respuesta al usuario usando WhatsApp Business
            send_whatsapp_message(sender_phone_number, ai_response)

            app.logger.info(f"Respuesta enviada al usuario: {ai_response}")
            return jsonify({"message": "Respuesta enviada correctamente."}), 200

    except Exception as e:
        app.logger.error(f"Error en el endpoint /api/v1/chat/whatsapp: {str(e)}")
        return str(e), 500

@chatbot_api.route("/api/v1/chat/ia/images", methods=["POST"])
def chat_ia_images_endpoint():
    try:
        data = request.json
        phone_number = data.get("phone_number")
        prompt = data.get("prompt")
        image_url = data.get("image_url")

        if not phone_number or not prompt:
            return jsonify({"error": "Faltan parámetros requeridos."}), 400

        # Lógica de chat simplificada
        ai_response = chat_logic_simplified(phone_number, prompt, image_url=image_url)

        return jsonify({"response": ai_response}), 200

    except Exception as e:
        app.logger.error(f"Error en el endpoint /api/v1/chat/ia/images: {str(e)}")
        return jsonify({"error": str(e)}), 500

@chatbot_api.route("/api/v1/files/to_base64", methods=["POST"])
def files_to_base64():
    try:
        data = request.json
        if not isinstance(data, list) or not data:
            return jsonify({"error": "El cuerpo de la solicitud debe ser una lista no vacía."}), 400

        results = []
        for item in data:
            if not item.get("ok") or not item.get("result"):
                continue

            file_info = item["result"]
            file_path = file_info.get("file_path")
            if not file_path:
                continue

            # Construir la URL del archivo
            telegram_file_url = f"https://api.telegram.org/file/bot7910952063:AAHRyczdhce6_UdOwj8Kr07n4mYyxP7B2fA/{file_path}"
            app.logger.info(f"Descargando archivo desde: {telegram_file_url}")

            # Descargar el archivo
            response = requests.get(telegram_file_url, timeout=10)
            if response.status_code != 200:
                app.logger.error(f"Error al descargar el archivo: {response.status_code}")
                continue

            # Convertir el archivo a Base64
            file_base64 = base64.b64encode(response.content).decode("utf-8")
            results.append({
                "file_id": file_info.get("file_id"),
                "file_base64": file_base64
            })

        if not results:
            return jsonify({"error": "No se pudo procesar ningún archivo."}), 400

        return jsonify({"files": results}), 200

    except Exception as e:
        app.logger.error(f"Error en el endpoint /api/v1/files/to_base64: {str(e)}")
        return jsonify({"error": str(e)}), 500

@chatbot_api.route("/api/v1/files/save_image", methods=["POST"])
def save_image_to_static():
    try:
        data = request.json
        if not isinstance(data, list) or not data:
            return jsonify({"error": "El cuerpo de la solicitud debe ser una lista no vacía."}), 400

        results = []
        static_folder = os.path.join(os.path.dirname(__file__), '..', 'static')

        # Crear la carpeta 'static' si no existe
        if not os.path.exists(static_folder):
            os.makedirs(static_folder)
            app.logger.info(f"Carpeta 'static' creada en: {static_folder}")

        for item in data:
            # Extraer el objeto anidado dentro de la lista
            if not isinstance(item, dict) or "Object" not in item:
                app.logger.error("Formato inválido: falta la clave 'Object'.")
                continue

            file_info = item["Object"]
            file_path = file_info.get("file_path")
            if not file_path:
                app.logger.error("Formato inválido: falta la clave 'file_path'.")
                continue

            # Construir la URL del archivo en Telegram
            telegram_file_url = f"https://api.telegram.org/file/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/{file_path}"
            app.logger.info(f"Descargando archivo desde: {telegram_file_url}")

            # Descargar y guardar la imagen usando la función `download_image_from_url`
            image_path = download_image_from_url(telegram_file_url)
            if not image_path:
                app.logger.error(f"No se pudo descargar o guardar la imagen desde: {telegram_file_url}")
                continue

            # Construir la URL completa accesible públicamente
            file_name = os.path.basename(image_path)
            server_url = request.host_url.rstrip('/')  # Obtener la URL base del servidor
            public_url = f"{server_url}/static/{file_name}"
            results.append({
                "file_id": file_info.get("file_id"),
                "file_url": public_url
            })

        if not results:
            return jsonify({"error": "No se pudo procesar ningún archivo."}), 400

        return jsonify({"files": results}), 200

    except Exception as e:
        app.logger.error(f"Error en el endpoint /api/v1/files/save_image: {str(e)}")
        return jsonify({"error": str(e)}), 500