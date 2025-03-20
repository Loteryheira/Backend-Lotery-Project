from flask import Blueprint, request, jsonify, current_app as app
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from src.database.db import friends_collection, chat_sessions_collection, sales_collection, comprobantes_collection
from datetime import datetime
import openai
import os
from dotenv import load_dotenv
import random
import re
from PIL import Image
import re 
import requests
from io import BytesIO
from google import genai
from google.genai import types
import imaplib
import email
from email.header import decode_header

load_dotenv()

chatbot_api = Blueprint("chatbot_api", __name__)

client = openai.Client()
openai.api_key = os.getenv("OPENAI_API_KEY")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

#------------------------- Función para extraer la referencia y el monto de un correo --------------------------    


def extract_reference_from_email():
    email_user = os.getenv("EMAIL_USER")
    email_pass = os.getenv("EMAIL_PASS")
    imap_server = os.getenv("IMAP_SERVER")
    imap_port = int(os.getenv("IMAP_PORT", 993))

    try:
        # Conectar al servidor IMAP
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
        mail.login(email_user, email_pass)
        mail.select("inbox")

        # Buscar correos no leídos del remitente específico con el asunto específico
        status, messages = mail.search(None, '(UNSEEN FROM "adrianrincon102001@gmail.com" SUBJECT "comprobante de transacción SINPE")')
        email_ids = messages[0].split()

        for email_id in email_ids:
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = decode_header(msg["Subject"])[0][0]
                    if isinstance(subject, bytes):
                        subject = subject.decode()

                    # Procesar el contenido del correo
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))

                            if "attachment" not in content_disposition:
                                body = part.get_payload(decode=True).decode()
                                # Buscar la referencia y el monto en el cuerpo del correo
                                referencia = re.search(r'Referencia SINPE:\s+(\d{20,30})', body)
                                monto = re.search(r'Monto Neto:\s+([\d,\.]+)', body)
                                if referencia and monto:
                                    return referencia.group(1), monto.group(1)
                    else:
                        body = msg.get_payload(decode=True).decode()
                        referencia = re.search(r'Referencia SINPE:\s+(\d{20,30})', body)
                        monto = re.search(r'Monto Neto:\s+([\d,\.]+)', body)
                        if referencia and monto:
                            return referencia.group(1), monto.group(1)

        mail.logout()
        return None, None
    except Exception as e:
        print(f"Error al leer el correo: {str(e)}")
        return None, None

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
        
        # Autenticación con las credenciales de Twilio
        response = requests.get(image_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=10)
        response.raise_for_status()

        # Verificar el tipo de contenido
        content_type = response.headers.get('Content-Type', '')
        if 'image' not in content_type:
            app.logger.info(f"El contenido descargado no es una imagen. Content-Type: {content_type}")
            return None

        # Abrir la imagen
        image = Image.open(BytesIO(response.content))

        # Verificar si la carpeta 'static' existe, si no, crearla
        static_folder = os.path.join(os.path.dirname(__file__), '..', 'static')
        if not os.path.exists(static_folder):
            os.makedirs(static_folder)
            app.logger.info(f"Carpeta 'static' creada en: {static_folder}")

        # Guardar la imagen
        image_path = os.path.join(static_folder, "downloaded_image.png")
        image.save(image_path)
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

# Luego, en tu función chat_logic_simplified, puedes usar download_image_from_url para descargar la imagen
def chat_logic_simplified(phone_number, prompt, ai_name=None, audio_url=None, image_url=None):
    user_name = "mi amor"

    if ai_name is None:
        return "¡Ay mi Dios! Algo salió mal, vuelva a intentarlo más tarde."

    try:
        ia_info = friends_collection.find_one({"name": "Tía Maria"})
        if not ia_info:
            return "¡Upe! La Tía María está ocupada, intente más tarde."

        atributos = ia_info.get('atributos', {})
        modismos = atributos.get('estilo_comunicacion', {}).get('modismos', ['mae', 'pura vida'])
        frases_venta = ia_info.get('frases_venta', [])
        cierres = ia_info.get('cierre_venta', {}).get('frases', [])

        # Buscar o crear una sesión de chat existente
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
                "ronda": "",
                "ultima_actualizacion": datetime.now().isoformat(),
                "apuestas": []  # Nuevo campo para almacenar apuestas detalladas
            }
            chat_session_id = chat_sessions_collection.insert_one(chat_session).inserted_id
        else:
            chat_session_id = chat_session["_id"]

        etapa_venta = chat_session.get("etapa_venta", "inicio")
        numeros = chat_session.get("numeros", [])
        monto = chat_session.get("monto", 0)
        referencia_pago = chat_session.get("referencia_pago", "")
        ronda = chat_session.get("ronda", "")
        apuestas = chat_session.get("apuestas", [])

        # Manejo de saludos y despedidas con IA
        if etapa_venta == "inicio" or "hola" in prompt.lower():
            # Saludo inicial con IA y explicación del sistema
            ai_response = (
                "¡Hola sobrin@! Bienvenido al sistema de tiempos apuntados. "
                "Por favor, indícame los números que deseas apuntar y en qué sorteo (1pm, 4pm, 7pm). "
                "Por ejemplo: 'Quiero apuntar 200 al 8 para las 1pm, 400 al 9 para las 4pm y 150 al 10 para las 7pm'.\n"
                "¡Buena suerte!"
            )
            etapa_venta = "solicitar_numeros"

        elif etapa_venta == "solicitar_numeros":
            try:
                # Analizar el mensaje para obtener números, montos y rondas
                apuestas_raw = re.findall(r'(\d+)\s+al\s+(\d{1,2})\s+para\s+las\s+(\d{1,2}(?:am|pm))', prompt)
                if not apuestas_raw:
                    raise ValueError("Formato de apuesta no válido.")

                apuestas_detalle = []
                total_monto = 0

                for monto_str, numero, ronda in apuestas_raw:
                    monto = int(monto_str)
                    total_monto += monto
                    ronda = ronda.lower()

                    # Verificar que la suma total de las apuestas para cada número no exceda los 6000
                    total_apostado = sum(
                        bet["monto"] for bet in sales_collection.find({"numero": numero, "ronda": ronda})
                    )
                    if total_apostado + monto > 6000:
                        return (
                            f"¡Upe! 😅 El apuntado total para el número {numero} "
                            f"excede los ¢6000 permitidos para esta ronda. "
                            f"Monto disponible: ¢{6000 - total_apostado}"
                        )

                    apuestas_detalle.append({"numero": numero, "ronda": ronda, "monto": monto})

                ai_response = (
                    f"¡Listo! 💵 Apuntando un total de ¢{total_monto:,}.\n"
                    "**Instrucciones de pago:**\n"
                    "1. Transfiere al SINPE MÓVIL: 8888-8888\n"
                    "2. Envíe el NÚMERO DE REFERENCIA de su comprobante o una captura de pantalla\n"
                    f"{random.choice(cierres)} 🍀"
                )
                etapa_venta = "validar_pago"
                numeros = [bet["numero"] for bet in apuestas_detalle]
                apuestas = apuestas_detalle

            except Exception as e:
                print(f"Error monto: {str(e)}")
                ai_response = "¡Upe! 😅 Monto inválido o ronda no especificada."

        elif etapa_venta == "validar_pago":
            if image_url:
                # Descargar la imagen desde la URL
                image_path = download_image_from_url(image_url)
                if image_path:
                    app.logger.info(f"Imagen descargada y guardada en: {image_path}")
                    api_key = os.getenv("GEMINI_API_KEY")  # Asegúrate de que esta clave esté configurada
                    extracted_text = extract_text_from_image_with_gemini(image_path, api_key)
                    if extracted_text:
                        app.logger.info(f"Texto extraído completo: {extracted_text}")  # Depuración
                        # Buscar el número de referencia en el texto extraído
                        referencia = re.search(r'\b\d{20,30}\b', extracted_text)
                        if referencia:
                            referencia_pago = referencia.group()
                            app.logger.info(f"Referencia extraída: {referencia_pago}")
                            # Actualizar el prompt con la referencia extraída
                            prompt += f" Referencia: {referencia_pago}"
                        else:
                            return "No se encontró el número de referencia en la imagen."
                    else:
                        return "No se pudo extraer texto de la imagen."
                else:
                    return "No se pudo descargar la imagen."
            else:
                referencia = re.search(r'\b\d{20,30}\b', prompt)
                if referencia:
                    referencia_pago = referencia.group()
                    app.logger.info(f"Referencia extraída: {referencia_pago}")
                else:
                    return "No se encontró el número de referencia en el mensaje."
                
            # Verificar si la referencia existe y no ha sido usada
            comprobante = comprobantes_collection.find_one({"referencia": referencia_pago, "usado": False})
            if comprobante:
                factura = (
                    f"📄 **COMPROBANTE OFICIAL**\n"
                    f"📱 Cliente: {phone_number}\n"
                    f"🔢 Números y Rondas:\n"
                )

                for apuesta in apuestas:
                    numero = apuesta["numero"]
                    ronda = apuesta["ronda"]
                    monto = apuesta["monto"]
                    factura += f"- Número: {numero}, Ronda: {ronda}, Monto: ¢{monto:,}\n"

                    # Guardar la apuesta con el ID del registro
                    sales_record = sales_collection.insert_one({
                        "telefono": phone_number,
                        "numero": numero,
                        "monto": monto,
                        "referencia": referencia_pago,
                        "ronda": ronda,
                        "fecha": datetime.now().isoformat(),
                        "factura": factura
                    })

                    # Incluir el ID del registro en la factura
                    factura += f"- ID de Registro: {sales_record.inserted_id}\n"

                factura += f"💵 Monto Total: ¢{sum(apuesta['monto'] for apuesta in apuestas):,}\n"
                factura += f"📅 Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
                factura += "¡Gracias por jugar con nosotros! 🍀"

                ai_response = (
                    f"✅ Validado\n\n{factura}\n\n"
                    "Guarde este comprobante como respaldo oficial. "
                    "¡Buena suerte sobrin@! 😊"
                    "¡No se hacen cambios una vez realizada la transaccion!"
                )
                etapa_venta = "finalizar"
                numeros = []
                monto = 0
                apuestas = []

                # Marcar la referencia como usada
                comprobantes_collection.update_one(
                    {"referencia": referencia_pago},
                    {"$set": {"usado": True}}
                )

                # No enviar mensajes adicionales después de finalizar
                return ai_response

            else:
                ai_response = (
                    "¡Ay mi Dios! 😱 Esta referencia ya ha sido utilizada o no es válida. "
                    "Por favor, proporcione una referencia válida y no utilizada."
                )

        # Manejo de mensajes inesperados
        elif etapa_venta in ["solicitar_numeros", "solicitar_monto", "validar_pago"]:
            # Si el mensaje no coincide con la etapa actual, la IA responde y redirige
            ai_response = generate_ai_response(ia_info, user_name, prompt, is_greeting=False, phone_number=phone_number, audio_url=audio_url)
            ai_response += "\n\nVolvamos al proceso de venta. ¿En qué puedo ayudarte con tu apuesta?"

        # Despedida con IA
        if etapa_venta == "finalizar":
            ai_response += "\n\n" + generate_ai_response(ia_info, user_name, prompt, is_greeting=False, phone_number=phone_number, audio_url=audio_url)

        # Actualización de base de datos
        update_data = {
            "etapa_venta": etapa_venta,
            "numeros": numeros,
            "monto": monto,
            "referencia_pago": referencia_pago,
            "ronda": "",  # No se necesita almacenar una ronda general
            "apuestas": apuestas,
            "ultima_actualizacion": datetime.now().isoformat()
        }

        chat_sessions_collection.update_one(
            {"_id": chat_session_id},
            {
                "$set": update_data,
                "$push": {
                    "chat_history": {
                        "$each": [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": ai_response}
                        ],
                        "$slice": -20
                    }
                }
            }
        )

        return ai_response

    except Exception as e:
        print(f"Error crítico: {str(e)}")
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


@chatbot_api.route("/api/v1/chat/twilio", methods=["POST"])
def chat_twilio_endpoint():
    try:
        incoming_msg = request.values.get("Body", "").strip()
        sender_phone_number = request.values.get("From", "").strip()
        media_url = request.values.get("MediaUrl0", "").strip()

        app.logger.info(f"Mensaje recibido: {incoming_msg}")
        app.logger.info(f"Número del remitente: {sender_phone_number}")
        app.logger.info(f"URL de la imagen recibida: {media_url}")

        if not incoming_msg and not media_url:
            app.logger.info("El mensaje recibido está vacío y no contiene una URL de imagen.")
            return "No se recibió ningún mensaje ni imagen.", 400

        ai_response = chat_logic_simplified(
            sender_phone_number, incoming_msg, ai_name="Tía Maria", image_url=media_url
        )

        resp = MessagingResponse()
        msg = resp.message()
        msg.body(ai_response)

        app.logger.info(f"Respuesta enviada al usuario: {ai_response}")
        return str(resp)
    except Exception as e:
        app.logger.info(f"Error en el endpoint /api/v1/chat/twilio: {str(e)}")
        return str(e), 500

@chatbot_api.route("/api/v1/sms", methods=["POST"])
def handle_sms():
    try:
        body = request.form.get("Body", "Hola, aquí está mi comprobante: 12345678901234567890")
        
        message = twilio_client.messages.create(
            from_='+12533667729',
            body=body,
            to='+18777804236'
        )
        print(message.sid)

        sender_phone_number = '+12533667729'
        expected_sender = "+12533667729"
        expected_receiver = "+18777804236"

        if sender_phone_number != expected_sender:
            return "Número de origen no autorizado.", 403

        if '+18777804236' != expected_receiver:
            return "Número de destino no autorizado.", 403

        comprobante_match = re.search(r'\b\d{20}\b', body)
        if comprobante_match:
            referencia_pago = comprobante_match.group()

            comprobantes_collection.insert_one({
                "telefono": sender_phone_number,
                "referencia": referencia_pago,
                "fecha": datetime.now().isoformat(),
                "mensaje": body,
                "usado": False
            })

            return "SMS enviado y registrado correctamente.", 200
        else:
            return "No se encontró un número de comprobante válido.", 400

    except Exception as e:
        return str(e), 500