from flask import Blueprint, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from src.database.db import friends_collection, chat_sessions_collection, sales_collection
from datetime import datetime
import openai
import os
from dotenv import load_dotenv
import random
import re

load_dotenv()

chatbot_api = Blueprint("chatbot_api", __name__)

client = openai.Client()
openai.api_key = os.getenv("OPENAI_API_KEY")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def chat_logic_simplified(phone_number, prompt, ai_name=None, audio_url=None):
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
        
        chat_session = chat_sessions_collection.find_one(
            {"phone_number": phone_number, "ia_name": "Tía Maria"}
        )
        
        etapa_venta = "inicio"
        numeros = []
        monto = 0
        referencia_pago = ""
        
        if chat_session:
            etapa_venta = chat_session.get("etapa_venta", "inicio")
            numeros = chat_session.get("numeros", [])
            monto = chat_session.get("monto", 0)
            referencia_pago = chat_session.get("referencia_pago", "")
        
        # Lógica mejorada con manejo de comprobantes
        if etapa_venta == "inicio":
            ai_response = (
                f"{random.choice(modismos).capitalize()} {user_name} 😊 "
                f"{random.choice(frases_venta)} "
                "Necesito 6 números diferentes entre 01 y 36.\n"
                "Ejemplo válido: 05, 12, 18, 23, 30, 35"
            )
            etapa_venta = "solicitar_numeros"
            numeros = []
            monto = 0
            
        elif etapa_venta == "solicitar_numeros":
            try:
                numeros_raw = re.findall(r'\b\d{1,2}\b', prompt)
                numeros = [n.zfill(2) for n in numeros_raw if n.isdigit()]
                
                # Validación corregida con paréntesis correctos
                if len(numeros) != 6 or len(set(numeros)) != 6 or any(not (1 <= int(n) <= 36) for n in numeros):
                    raise ValueError
                
                numeros = sorted(numeros)
                ai_response = (
                    f"¡Buena elección! 🎰 Números: {', '.join(numeros)}\n"
                    f"{random.choice(modismos).capitalize()} ¿Cuánto va a apostar? (Mínimo ¢200)"
                )
                etapa_venta = "solicitar_monto"
                
            except Exception as e:
                print(f"Error validación: {str(e)}")
                ai_response = (
                    f"¡Ay mi Dios {user_name}! 😅\n"
                    "Deben ser 6 números ÚNICOS entre 01 y 36\n"
                    "Ejemplo: 05, 12, 18, 23, 30, 35"
                )
                numeros = []
                
        elif etapa_venta == "solicitar_monto":
            try:
                monto = int(''.join(filter(str.isdigit, prompt)))
                if monto < 200:
                    raise ValueError
                
                ai_response = (
                    f"¡Listo! 💵 Apostando ¢{monto:,}\n"
                    "**Instrucciones de pago:**\n"
                    "1. Transfiera al SINPE MÓVIL: 8888-8888\n"
                    "2. Envíe el NÚMERO DE REFERENCIA de su comprobante\n"
                    f"{random.choice(cierres)} 🍀"
                )
                etapa_venta = "validar_pago"
                
            except:
                ai_response = "¡Upe! 😅 Monto inválido. Mínimo ¢200"
                
        elif etapa_venta == "validar_pago":
            referencia = re.search(r'\b\d{10}\b', prompt)
            if referencia:
                referencia_pago = referencia.group()
                
                # Generar y guardar factura
                factura = (
                    f"📄 **COMPROBANTE OFICIAL**\n"
                    f"📱 Cliente: {phone_number}\n"
                    f"🔢 Números: {', '.join(numeros)}\n"
                    f"💵 Monto: ¢{monto:,}\n"
                    f"📟 Referencia: {referencia_pago}\n"
                    f"📅 Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
                    "¡Gracias por jugar con nosotros! 🍀"
                )
                
                # Registrar venta
                sales_collection.insert_one({
                    "telefono": phone_number,
                    "numeros": numeros,
                    "monto": monto,
                    "referencia": referencia_pago,
                    "fecha": datetime.now().isoformat(),
                    "factura": factura
                })
                
                ai_response = (
                    f"✅ Pago validado\n\n{factura}\n\n"
                    "Guarde este comprobante como respaldo oficial. "
                    "¡Buena suerte mi amor! 😊"
                )
                etapa_venta = "inicio"
                numeros = []
                monto = 0
            else:
                ai_response = (
                    "¡Ay mi Dios! 😱 No encontré el número de referencia\n"
                    "Debe ser un número de 10 dígitos del comprobante\n"
                    "Ejemplo válido: 1234567890"
                )
        
        # Actualización de base de datos
        update_data = {
            "etapa_venta": etapa_venta,
            "numeros": numeros,
            "monto": monto,
            "referencia_pago": referencia_pago,
            "ultima_actualizacion": datetime.now().isoformat()
        }
        
        if chat_session:
            chat_sessions_collection.update_one(
                {"_id": chat_session["_id"]},
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
        else:
            chat_sessions_collection.insert_one({
                "phone_number": phone_number,
                "ia_name": "Tía Maria",
                "chat_history": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": ai_response}
                ],
                **update_data
            })

        return ai_response

    except Exception as e:
        print(f"Error crítico: {str(e)}")
        return "¡Ay mi Dios! Se me cruzaron los cables. ¿Me repite mi amor?"


@chatbot_api.route("/api/v1/amigo", methods=["POST"])
def create_friend():
    try:
        data = request.json
        
        # Validar campos obligatorios
        required_fields = ["name", "description", "atributos", "frases_venta", "cierre_venta"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Campo requerido faltante: {field}"}), 400

        # Estructura completa con valores por defecto
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

        # Llamar a la lógica del chat simplificada
        ai_response = chat_logic_simplified(
            sender_phone_number, incoming_msg, ai_name="Tía Maria"
        )

        # Preparar la respuesta para Twilio
        resp = MessagingResponse()
        msg = resp.message()
        msg.body(ai_response)

        return str(resp)
    except Exception as e:
        return str(e), 500
