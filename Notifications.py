import mysql.connector
import firebase_admin
from firebase_admin import credentials, messaging
import openai
import requests
import time
import json
from datetime import datetime

# Conf
DB_CONFIG = {
    'host': '***************',
    'user': '***********',
    'password': '************',
    'database': '***********'
}

TELEGRAM_BOT_TOKEN = '8***********'
TELEGRAM_CHAT_ID = '***********'
OPENAI_API_KEY = '***********'
FIREBASE_CRED_PATH = '***********'

openai.api_key = OPENAI_API_KEY

if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred)

INCLUDE_CUSTOMER_DETAILS_IN_TELEGRAM = True  # غيّرها لـ False إذا ما تبي ترسل بيانات العميل

LAST_ID_FILE = 'last_rating_id.json'

def get_last_processed_info():
    try:
        with open(LAST_ID_FILE, 'r') as file:
            data = json.load(file)
            return data.get('last_id', 0), data.get('last_time', '2000-01-01 00:00:00')
    except FileNotFoundError:
        return 0, '2000-01-01 00:00:00'

def save_last_processed_info(rating_id, created_at):
    with open(LAST_ID_FILE, 'w') as file:
        json.dump({'last_id': rating_id, 'last_time': created_at}, file)

def get_user_details(conn, user_id):
    try:
        cursor = conn.cursor(dictionary=True)
        query = "SELECT name, phone, rating_phone, created_at FROM users WHERE id = %s"
        cursor.execute(query, (user_id,))
        user = cursor.fetchone()
        return user if user else {}
    except Exception as e:
        print(f"❌ Error fetching user details: {e}")
        return {}

def analyze_feedback_with_openai(review, rating, customer_name=None):
    name_part = f"ياهلا {customer_name}،" if customer_name else ""
    prompt = f"""
    {name_part} العميل السعودي، قيّم تجربته في المتجر.
    التقييم: {rating} من 5.
    التعليق: {review if review else 'ما كتب تعليق'}.

    اكتب له رد مختصر جدًا (ما يتجاوز 20 كلمة) باللهجة السعودية، يكون ذكي، لبق، وواقعي حسب تقييمه.
    الهدف إننا نكسبه ونخليه يرجع يطلب مرة ثانية، حتى لو كان التقييم سيء.
    """
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "أنت مساعد سعودي تكتب ردود لبقة ومختصرة للعملاء، كل رد لا يزيد عن 20 كلمة."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )
    return response.choices[0].message.content.strip()

def generate_advice_for_store_with_openai(review, rating, customer_name=None):
    name_part = f"العميل {customer_name}" if customer_name else "العميل"
    prompt = f"""
    {name_part} قيّم المتجر بـ {rating} من 5.
    كتب في تعليقه: "{review if review else 'ما كتب تعليق'}"

    اعطِ ملاحظة ذكية لصاحب المتجر عن كيف يتعامل مع العميل.
    إذا التقييم ضعيف: اقترح تعويض، تواصل لطيف، أو خصم.
    إذا التقييم ممتاز: اقترح طريقة لتحفيزه يرجع يطلب.
    خلك عملي وواقعي، ولا تطول (سطرين كحد أقصى)، وخلك باللهجة السعودية.
    """
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "أنت مستشار تسويق سعودي تعطي ملاحظات قصيرة وذكية للمتجر عن العملاء."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )
    return response.choices[0].message.content.strip()

def generate_telegram_message(customer_name, review, rating, user_details, order_id, smart_response, store_advice):
    user_info = ""
    if INCLUDE_CUSTOMER_DETAILS_IN_TELEGRAM and user_details:
        name = user_details.get('name', 'غير معروف')
        phone = user_details.get('phone', 'غير متوفر')
        rating_phone = user_details.get('rating_phone', 'غير متوفر')
        created_at = user_details.get('created_at', '')
        user_info = f"""
📇 بيانات العميل:
الاسم: {name}

 الجوال: {rating_phone}
تاريخ التسجيل: {created_at}
        """.strip()

    message = f"""✨ تقييم جديد

👤 العميل: {customer_name}
⭐️ التقييم: {rating}/5
💬: {review if review else 'ما كتب شيء'}

📩 الرد للعميل:
{smart_response}

🧠 نصيحة للمتجر:
{store_advice}

{user_info if user_info else ''}
"""
    return message

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {'chat_id': TELEGRAM_CHAT_ID, 'text': message}
    requests.post(url, data=data)

def send_firebase_notification(order_id, message):
    topic = f"Order_{order_id}"
    notification = messaging.Message(
        notification=messaging.Notification(
            title="💬 تقييمك يهمنا!",
            body=message
        ),
        topic=topic
    )
    response = messaging.send(notification)
    print(f"✅ Firebase notification sent to topic '{topic}': {response}")

def main_loop():
    print("📡 Starting continuous Smart Feedback Processor...\n")

    while True:
        last_id, last_time = get_last_processed_info()

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)

            query = """
            SELECT * FROM ratings
            WHERE created_at > %s
            ORDER BY created_at DESC
            LIMIT 1
            """
            cursor.execute(query, (last_time,))
            result = cursor.fetchone()

            if result:
                rating_id = result['id']
                order_id = result['order_id']
                rating = result['rating']
                review = result.get('review', '')
                user_id = result.get('user_id', None)
                created_at = result['created_at'].strftime('%Y-%m-%d %H:%M:%S')

                print(f"\n📋 New rating found at {created_at}:\n - ID: {rating_id}\n - Rating: {rating}\n - Review: {review}\n")

                user_details = get_user_details(conn, user_id) if user_id else {}
                customer_name = user_details.get('name') if user_details else None

                smart_response = analyze_feedback_with_openai(review, rating, customer_name)
                store_advice = generate_advice_for_store_with_openai(review, rating, customer_name)

                send_firebase_notification(order_id, smart_response)

                telegram_message = generate_telegram_message(
                    customer_name if customer_name else f"User {user_id}",
                    review,
                    rating,
                    user_details,
                    order_id,
                    smart_response,
                    store_advice
                )
                send_telegram_message(telegram_message)

                save_last_processed_info(rating_id, created_at)
                print("✅ Processed and saved.\n")
            else:
                print("🔍 No new ratings found. Retrying in 10 seconds...")

        except Exception as e:
            print(f"❌ Error: {e}")

        finally:
            if 'conn' in locals():
                conn.close()

        time.sleep(10)

# Start the loop
main_loop()
