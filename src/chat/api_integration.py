from flask import Blueprint, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from src.database.db import friends_collection, chat_sessions_collection
from datetime import datetime
import openai
import os
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
    user_name = "usuario"

    if ai_name is None:
        return "No se proporcionó un nombre de IA."
    ia_info = friends_collection.find_one({"name": ai_name})
    if ia_info is None:
        return f"No se encontró la IA con el nombre {ai_name}."
    ia_name = ia_info["name"]
    ia_description = ia_info["description"] if ia_info else "Soy un asistente virtual."

    # Verificar si existe un documento de entrenamiento y extraer su contenido
    training_content = ia_info.get("training_content", "")
    if training_content:
        introduction = f"Hi {user_name}, i'am {ia_name}, {ia_description} I am trained with the following information: {training_content[:500]}..."
    else:
        introduction = f"Hi {user_name}, i'am {ia_name}, {ia_description}"

    chat_session = chat_sessions_collection.find_one(
        {"phone_number": phone_number, "ia_name": ia_name}
    )
    if not chat_session:
        chat_session = {
            "phone_number": phone_number,
            "chat_history": [{"role": "system", "content": introduction}],
            "ia_name": ia_name,
            "favorite": False,
            "user_name": user_name,
        }
        chat_session_id = chat_sessions_collection.insert_one(chat_session).inserted_id
        chat_session = chat_sessions_collection.find_one({"_id": chat_session_id})
    else:
        chat_session_id = chat_session["_id"]

    chat_history = chat_session.get("chat_history", [])[-19:]
    messages = [
        {"role": msg.get("role", "user"), "content": msg.get("content", "")}
        for msg in chat_history
    ]
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
            response_parts = full_response.rsplit(". ", 1)
            ai_response = (
                response_parts[0] + "." if len(response_parts) > 1 else full_response
            )

        # Reemplazar solo los puntos que no son parte de una lista
        lines = ai_response.split("\n")
        for i, line in enumerate(lines):
            # Asumiendo que las listas comienzan con '- ' o con un número seguido de '.'
            if not line.lstrip().startswith("- ") and not line.lstrip().startswith(
                tuple(f"{n}. " for n in range(1, 11))
            ):
                lines[i] = line.replace(". ", ".\n")
        ai_response = "\n".join(lines)

    except Exception as e:
        print(f"Error inesperado: {e}")
        ai_response = "Ocurrió un error al llamar a la API de OpenAI."

    chat_sessions_collection.update_one(
        {"_id": chat_session_id},
        {
            "$push": {
                "chat_history": {
                    "$each": [
                        {
                            "user_message": prompt,
                            "ai_response": ai_response,
                            "audio_url": audio_url,
                            "timestamp": datetime.now(),
                            "favorite": False,
                        }
                    ],
                    "$slice": -20,
                }
            }
        },
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

        # Llamar a la lógica del chat simplificada
        ai_response = chat_logic_simplified(
            sender_phone_number, incoming_msg, ai_name="tia maria"
        )

        # Preparar la respuesta para Twilio
        resp = MessagingResponse()
        msg = resp.message()
        msg.body(ai_response)

        return str(resp)
    except Exception as e:
        return str(e), 500
