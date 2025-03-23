import imaplib
import email
import os
from dotenv import load_dotenv
import re
import datetime
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

from src.database.db import comprobantes_collection

load_dotenv()

def extraer_mensajes_gmail(remitente):
    print("[DEBUG] Iniciando proceso...")
    usuario = os.getenv("EMAIL_USER")
    contraseña = os.getenv("EMAIL_PASS")
    imap_server = os.getenv("IMAP_SERVER")
    
    try:
        # 1. Conexión IMAP
        imap = imaplib.IMAP4_SSL(imap_server, 993)
        imap.login(usuario, contraseña)
        imap.select("inbox")

        # 2. Buscar mensajes NO LEÍDOS del remitente
        status, mensajes = imap.search(None, f'(UNSEEN FROM "{remitente}")')
        if status != "OK" or not mensajes[0]:
            print("No hay mensajes nuevos sin leer")
            return

        # 3. Procesar todos los mensajes encontrados
        for msg_id in mensajes[0].split():
            print(f"\n[DEBUG] Procesando mensaje ID: {msg_id}")

            # 4. Extraer contenido del mensaje
            status, data = imap.fetch(msg_id, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            
            # 5. Decodificar cuerpo del mensaje
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body += part.get_payload(decode=True).decode(errors="replace")
            else:
                body = msg.get_payload(decode=True).decode(errors="replace")

            # 6. Extraer datos con regex
            referencia = re.search(r"Referencia SINPE:\s*(\d+)", body)
            monto = re.search(r"por un monto de (\d+\.\d{2})\s*CRC", body)

            if not referencia or not monto:
                print("[ERROR] Datos incompletos en el mensaje")
                continue

            # 7. Verificar si la referencia ya existe
            existe = comprobantes_collection.find_one({
                "referencia": referencia.group(1)
            })

            if existe:
                print(f"[INFO] Referencia {referencia.group(1)} ya existe en BD")
                # Marcar como leído igualmente para no reprocesar
                imap.store(msg_id, '+FLAGS', '\\Seen')
                continue

            # 8. Insertar nuevo documento si no existe
            doc = {
                "email": remitente,
                "referencia": referencia.group(1),
                "fecha": datetime.datetime.now().isoformat(),
                "mensaje": f"Comprobante {referencia.group(1)}",
                "usado": False,
                "monto": float(monto.group(1))
            }

            result = comprobantes_collection.insert_one(doc)
            print(f"[ÉXITO] Insertado documento ID: {result.inserted_id}")
            
            # 9. Marcar correo como leído
            imap.store(msg_id, '+FLAGS', '\\Seen')

    except Exception as e:
        print(f"\n[ERROR CRÍTICO] {str(e)}")
    finally:
        imap.close()
        imap.logout()

extraer_mensajes_gmail("adrianrincon102001@gmail.com")