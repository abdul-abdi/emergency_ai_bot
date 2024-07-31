from flask import Flask, request, Response, send_file
import africastalking
import os
import pyttsx3
import logging
import openai
import asyncio
from dotenv import load_dotenv
from geopy.geocoders import Nominatim

app = Flask(__name__)

# Load environment variables
load_dotenv()

# Initialize AfricasTalking
username = os.getenv("AT_USERNAME")
api_key = os.getenv("AT_API_KEY")
africastalking.initialize(username, api_key)
voice = africastalking.Voice
sms = africastalking.SMS
openai.api_key = os.getenv("OPENAI_API_KEY")

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Function to transcribe audio using OpenAI
def transcribe_audio(file_path):
    with open(file_path, "rb") as audio_file:
        transcript = openai.Audio.transcriptions.create(
            file=audio_file,
            model="whisper-1"
        )
    return transcript['text']

@app.route('/location', methods=['POST'])
def handle_location():
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')
    
    geolocator = Nominatim(user_agent="emergency_app")
    location = geolocator.reverse(f"{latitude}, {longitude}")
    
    # Store the location in the session or a database for future use
    # For now, we'll just return it as part of the response
    
    disaster_info = get_disaster_info("Current disasters", location.address)
    
    response = f"<?xml version='1.0' encoding='UTF-8'?><Response><Say>Based on your location at {location.address}, here's the current disaster information: {disaster_info}</Say></Response>"
    return Response(response, mimetype='text/xml')

# Function to generate AI response using OpenAI
def generate_response(prompt):
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=150
    )
    return response.choices[0].text.strip()

# Function to convert text to speech and save as an audio file
def text_to_speech(text):
    engine = pyttsx3.init()
    engine.save_to_file(text, 'static/response.mp3')
    engine.runAndWait()

# Async wrapper for text to speech conversion
async def async_text_to_speech(text):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, text_to_speech, text)

# Function to send SMS
def send_sms(phone_number, message):
    try:
        response = sms.send(message, [phone_number])
        logging.debug(f"SMS Response: {response}")
        if response['SMSMessageData']['Recipients'][0]['status'] != 'Success':
            logging.error(f"Failed to send SMS: {response['SMSMessageData']['Recipients'][0]['status']}")
    except Exception as e:
        logging.error(f"Error sending SMS: {e}")

# Function to map DTMF digits to facility information
def get_facility_info(dtmf_digits):
    facility_info_map = {
        "1": "Facility 1: Safe House at 123 Main St.",
        "2": "Facility 2: Community Center at 456 Elm St.",
        # Add more mappings as needed
    }
    return facility_info_map.get(dtmf_digits, "Invalid selection.")

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_file(f'static/{filename}')

def get_disaster_info(query, location=None):
    # This function would interact with a disaster information database
    # and return relevant information based on the query and location
    prompt = f"Provide disaster information for the following query: {query}"
    if location:
        prompt += f" in the area of {location}"
    
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=200
    )
    return response.choices[0].text.strip()

@app.route('/voice', methods=['POST'])
def voice_callback():
    session_id = request.values.get("sessionId", None)
    is_active = request.values.get("isActive", False)
    dtmf_digits = request.values.get("dtmfDigits", None)
    caller_number = request.values.get("callerNumber", None)

    logging.debug(f"Session ID: {session_id}")
    logging.debug(f"Is Active: {is_active}")
    logging.debug(f"DTMF Digits: {dtmf_digits}")
    logging.debug(f"Caller Number: {caller_number}")

    response = '<?xml version="1.0" encoding="UTF-8"?><Response>'

    if is_active == "1":
        if dtmf_digits:
            facility_info = get_facility_info(dtmf_digits)
            logging.debug(f"Facility Info: {facility_info}")
            
            # Generate the audio file asynchronously
            asyncio.run(async_text_to_speech(facility_info))
            
            # Use the full URL to the audio file
            audio_url = request.url_root + 'static/response.mp3'
            response += f'<Play url="{audio_url}"/>'
            
            send_sms(caller_number, facility_info)
        else:
            response += '<Say>Welcome to the emergency information system. For disaster information, press 1. To share your location, press 2. To speak your query, press 3.</Say>'
            response += '<Gather numDigits="1" action="/handle_menu" method="POST"/>'
    else:
        response += '<Say>Thank you for calling. Goodbye!</Say>'

    response += '</Response>'
    return Response(response, mimetype='text/xml')

@app.route('/handle_menu', methods=['POST'])
def handle_menu():
    digits = request.form.get('dtmfDigits')
    
    if digits == '1':
        disaster_info = get_disaster_info("Current disasters")
        response = f'<?xml version="1.0" encoding="UTF-8"?><Response><Say>{disaster_info}</Say></Response>'
    elif digits == '2':
        response = '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Please text your location to this number in the format: LOCATION latitude,longitude</Say></Response>'
    elif digits == '3':
        response = '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Please speak your query after the beep.</Say><Record maxLength="30" finishOnKey="#" playBeep="true" /></Response>'
    else:
        response = '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Invalid option. Goodbye.</Say></Response>'
    
    return Response(response, mimetype='text/xml')


@app.route('/recording', methods=['POST'])
def recording_callback():
    recording_url = request.values.get("recordingUrl", None)
    caller_number = request.values.get("callerNumber", None)

    logging.info(f"Recording URL: {recording_url}")
    logging.info(f"Caller Number: {caller_number}")

    if not recording_url:
        logging.error("No recording URL received")
        return Response("<?xml version='1.0' encoding='UTF-8'?><Response><Say>Sorry, we couldn't process your request due to missing recording URL.</Say></Response>", mimetype='text/xml')

    try:
        # Download the recording
        recording_path = 'static/recording.mp3'
        download_result = os.system(f"wget -O {recording_path} {recording_url}")
        if download_result != 0:
            raise Exception(f"Failed to download recording. wget exit code: {download_result}")

        # Transcribe the recording
        transcription = transcribe_audio(recording_path)
        logging.info(f"Transcription: {transcription}")

        # Generate a response from OpenAI
        ai_response = generate_response(transcription)
        logging.info(f"AI Response: {ai_response}")

        # Generate the audio file asynchronously
        asyncio.run(async_text_to_speech(ai_response))

        # Use the full URL to the audio file
        audio_url = request.url_root + 'static/response.mp3'
        response = f'<?xml version="1.0" encoding="UTF-8"?><Response><Play url="{audio_url}"/></Response>'
        return Response(response, mimetype='text/xml')
    except Exception as e:
        logging.error(f"Error processing recording: {str(e)}")
        error_message = "An error occurred while processing your request. Please try again later."
        return Response(f"<?xml version='1.0' encoding='UTF-8'?><Response><Say>{error_message}</Say></Response>", mimetype='text/xml')
    
@app.route('/incoming_sms', methods=['POST'])
def handle_incoming_sms():
    message = request.form.get('text')
    sender = request.form.get('from')
    
    if message.startswith('LOCATION'):
        _, coords = message.split(' ', 1)
        latitude, longitude = coords.split(',')
        
        geolocator = Nominatim(user_agent="emergency_app")
        location = geolocator.reverse(f"{latitude}, {longitude}")
        
        disaster_info = get_disaster_info("Current disasters", location.address)
        response_message = f"Based on your location at {location.address}, here's the current disaster information: {disaster_info}"
    else:
        response_message = "Invalid message format. To share your location, please send: LOCATION latitude,longitude"
    
    send_sms(sender, response_message)
    return "OK"

if __name__ == "__main__":
    os.makedirs('static', exist_ok=True)  # Ensure the static directory exists
    app.run(debug=True, host='0.0.0.0', port=5000)