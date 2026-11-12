import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(base_url=os.getenv("BASE_URL"), api_key=os.getenv("API_KEY"))

response = client.chat.completions.create(
    model="nvidia/nemotron-3.5-lightning:free",
    messages=[
        {
            "role": "user",
            "content": "qual a verdade da vida do universo e tudo que há nele?",
        },
    ],
    temperature=0.3,
    max_tokens=1000,
)

print(response.choices[0].message.content)
