from flask_pymongo import pymongo
import os

client = pymongo.MongoClient(os.getenv("MONGO_LOCAL"))

db = client.get_database("chatbot")

friends_collection = db.get_collection("friends")
chat_sessions_collection = db.get_collection("chat_sessions")