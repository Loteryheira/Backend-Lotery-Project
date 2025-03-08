from flask import Blueprint, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from src.database.db import friends_collection, chat_sessions_collection
from datetime import datetime
import openai
import os
import random
from dotenv import load_dotenv

load_dotenv()

chatbot_api = Blueprint("chatbot_api", __name__)

client = openai.Client()
openai.api_key = os.getenv("OPENAI_API_KEY")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

def chat_logic_simplified(phone_number, prompt, ai_name=None, audio_url=None):
    user_name = "mi amor"  # Cambiamos a apodo cariñoso

    if ai_name is None:
        return "¡Ay mi Dios! Algo salió mal, vuelva a intentarlo más tarde."
    
    ia_info = friends_collection.find_one({"name": ai_name.lower()})
    if not ia_info:
        return f"¡Upe! Parece que {ai_name} no está disponible. ¡Pura vida!"
    
    # Construir sistema de personalidad desde MongoDB
    training_content = f"""
    Eres {ia_info['name']}, {ia_info['description']} de {ia_info['detalles_extra']['region']}. 
    Características clave:
    - Personalidad: {', '.join(ia_info['atributos']['personalidad'])}
    - Modismos: {', '.join(ia_info['atributos']['estilo_comunicacion']['modismos'])}
    - Frases clave: {', '.join(ia_info['frases_venta'])}
    - Cultura: {', '.join(ia_info['detalles_extra']['referencias_culturales'])}

    Reglas estrictas:
    1. Usa máximo 2 oraciones por respuesta
    2. Siempre incluye 1 modismo costarricense
    3. Termina con frase de cierre de venta
    4. Usa emojis relevantes (1 por respuesta)
    5. Mantén tono cálido y familiar
    """

    system_prompt = {
        "role": "system",
        "content": training_content + "\n\nContexto: Vendes lotería para Heira. Sé persuasiva pero respetuosa."
    }

    # Manejo de sesión de chat
    chat_session = chat_sessions_collection.find_one(
        {"phone_number": phone_number, "ia_name": ia_info['name']}
    )
    
    if not chat_session:
        chat_history = [system_prompt]
    else:
        chat_history = chat_session.get("chat_history", [])
        # Asegurar system prompt está presente
        if not any(msg.get('role') == 'system' for msg in chat_history):
            chat_history.insert(0, system_prompt)

    # Construir mensajes para OpenAI
    messages = [msg for msg in chat_history if msg['role'] in ('system', 'user', 'assistant')][-5:]  # Mantener conversación corta
    messages.append({"role": "user", "content": prompt})

    try:
        # Generar respuesta
        response = client.chat.completions.create(
            model="gpt-4o-2024-05-13",
            messages=messages,
            temperature=0.75,
            max_tokens=100,
            stop=["\n", "¡Pura vida!", "..."],
        )
        
        ai_response = response.choices[0].message.content.strip()

        # Añadir cierre si falta
        cierres = ia_info['cierre_venta']['frases']
        if not any(cierre in ai_response for cierre in cierres):
            ai_response += " " + random.choice(cierres)

        # Limpiar formato
        ai_response = ai_response.replace('"', '').replace('*', '').replace('  ', ' ')
        
        # Añadir emoji si falta
        if not any(c in ai_response for c in ['🌟', '💰', '🍀', '😊']):
            ai_response += random.choice([' 🌟', ' 😊', ' 🍀'])

    except Exception as e:
        print(f"Error OpenAI: {str(e)}")
        ai_response = "¡Ay mi Dios! Se me cruzaron los cables. ¿Me repite mi amor?"

    # Actualizar historial
    new_message = {
        "role": "assistant",
        "content": ai_response,
        "timestamp": datetime.now()
    }
    
    if not chat_session:
        chat_session = {
            "phone_number": phone_number,
            "chat_history": [system_prompt, new_message],
            "ia_name": ia_info['name'],
            "favorite": False
        }
        chat_sessions_collection.insert_one(chat_session)
    else:
        chat_sessions_collection.update_one(
            {"_id": chat_session["_id"]},
            {"$push": {"chat_history": {"$each": [new_message], "$slice": -20}}}
        )

    return ai_response

@chatbot_api.route("/api/v1/amigo", methods=["POST"])
def create_friend():
    try:
        data = request.json
        friend = {
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "gender": data.get("gender", ""),
        }
        friends_collection.insert_one(friend)
        return jsonify({"message": "Amigo creado exitosamente."})
    except Exception as e:
        return str(e), 500


@chatbot_api.route("/api/v1/chat/twilio", methods=["POST"])
def chat_twilio_endpoint():
    try:
        incoming_msg = request.values.get("Body", "").strip()
        sender_phone_number = request.values.get("From", "").strip()

        ai_response = chat_logic_simplified(
            sender_phone_number, 
            incoming_msg, 
            ai_name="Tía Maria"  
        )

        # Forzar estilo costarricense en respuestas
        replacements = {
            "usted": "vos",
            "tú": "vos",
            "adiós": "cuídese",
            "buenos días": "buenas buenas"
        }
        for k, v in replacements.items():
            ai_response = ai_response.replace(k, v)

        resp = MessagingResponse()
        msg = resp.message()
        msg.body(ai_response[:600])  # Limitar longitud para SMS

        return str(resp)
    except Exception as e:
        error_msg = f"¡Upe! Algo salió mal: {str(e)}"
        resp = MessagingResponse()
        msg = resp.message()
        msg.body(error_msg)
        return str(resp), 500