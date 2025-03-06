import unittest
from flask import Flask
from src.chat.api_integration import chatbot_api

class TestApiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.register_blueprint(chatbot_api)
        cls.client = cls.app.test_client()

    def test_chat_twilio_endpoint(self):
        response = self.client.post('/api/v1/chat/twilio', data={
            'Body': 'Hola',
            'From': '+1234567890'
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.data.decode()) > 0)

if __name__ == '__main__':
    unittest.main()