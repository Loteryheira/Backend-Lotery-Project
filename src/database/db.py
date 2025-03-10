from flask_pymongo import pymongo
import os
from dotenv import load_dotenv

load_dotenv()

client = pymongo.MongoClient(os.getenv("MONGO_LOCAL"))

db = client.get_database("chatbot")

friends_collection = db.get_collection("friends")
chat_sessions_collection = db.get_collection("chat_sessions")
sales_collection = db.get_collection("sales")
comprobantes_collection = db.get_collection("comprobantes")