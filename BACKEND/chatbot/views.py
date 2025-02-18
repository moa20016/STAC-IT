from huggingface_hub import InferenceClient
from django.shortcuts import render
import requests
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.http import HttpResponse
import time
import uuid
import logging
import re



def generate_response(preference, options):
    response = f"Your preference: **{preference}**\nOptions:\n"
    for option in options:
        response += f"*{option}*\n"
    return response

TICKETMASTER_API_KEY = ""

logger = logging.getLogger(__name__)

def get_music_events(query):
    """Fetch music events from Ticketmaster API based on user query."""
    base_url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {
        "apikey": TICKETMASTER_API_KEY,
        "keyword": query,
        "classificationName": "Music",
        "sort": "date,asc",
        "size": 5, 
    }

    try:
        response = requests.get(base_url, params=params)
        if response.status_code == 200:
            data = response.json()
            events = data.get("_embedded", {}).get("events", [])
            if not events:
                return {"error": "No music events found for your query."}
            
            formatted_events = []
            for event in events:
                formatted_events.append({
                    "name": event.get("name", "Unknown Event"),
                    "date": event.get("dates", {}).get("start", {}).get("localDate", "Date not available"),
                    "venue": event.get("_embedded", {}).get("venues", [{}])[0].get("name", "Venue not available"),
                    "url": event.get("url", "#"),
                })
            
            return formatted_events
        else:
            logger.error(f"Error fetching events: {response.status_code} - {response.text}")
            return {"error": f"API error: {response.status_code}"}
    except Exception as e:
        logger.error(f"Exception occurred: {str(e)}")
        return {}

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = InferenceClient(api_key="")  # Hugging Face API key
api_key = ""  # Google Places API key

def get_place_details(place_id):
    base_url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "key": api_key,
        "fields": "name,formatted_address,rating,opening_hours,price_level"
    }
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        return response.json().get("result", {})
    else:
        logger.error(f"Error fetching place details: {response.status_code} - {response.text}")
        return {}

def google_places_text_search(api_key, query, location=None, radius=None):
    base_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "key": api_key
    }
    if location and radius:
        params.update({"location": location, "radius": radius})
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        results = response.json().get("results", [])
        detailed_results = []
        for place in results:
            place_id = place.get("place_id")
            if place_id:
                detailed_place = get_place_details(place_id)
                detailed_results.append(detailed_place)
        return detailed_results
    else:
        logger.error(f"Error fetching text search: {response.status_code} - {response.text}")
        return []
    

def parse_user_input(user_input):
    """Extracts location, preferences, and budget from user input."""
    location_match = re.search(r"Location: \s*([^,]+,\s*[A-Z]{2})", user_input)
    preferences_match = re.search(r"Preferences: \s*([^,]+)", user_input)
    budget_match = re.search(r"Budget: \s*\$?(\d+)", user_input)

    location = location_match.group(1) if location_match else None
    preferences = preferences_match.group(1) if preferences_match else "music"
    budget = int(budget_match.group(1)) if budget_match else None

    return location, preferences, budget

def generate_planner_response(user_input, request_id, timestamp, temp):
    location, preferences, budget = parse_user_input(user_input)
    places_data = google_places_text_search(api_key, user_input)
    music_data = get_music_events(preferences)

    if isinstance(places_data, dict) and "error" in places_data:
        return places_data["error"]

    prompt = f"""
    [Request ID: {request_id}]
    [Timestamp: {timestamp}]
    Help the user create a simple day plan based on GOOGLE_DATA: {places_data}\n, MUSIC_DATA: {music_data}\n and from user input: {user_input}.
        Extract their preferences from the user input. For EACH preference, suggest **exactly three high-rated places** 
        that match their interest, ensuring the places are open during the user's available hours. 
        Ensure each preference fits within the user's available hours and the location's open hours, 
        allowing 30 minutes between each preference for transportation. If it is music, prioritize from MUSIC DATA over GOOGLE DATA.

        For each preference in user input, provide:

        <Preference 1>: Label of the preference
        <Option 1>: Mention the name of the place.
        <Option 1 Activity Description>: Describe what the user will enjoy at each location, including recommended flavors, dishes, or highlights. Create an experience rather than saying something like "eat at a place".
        <Option 1 Location>: Add the address of the location.
        Timing: Mention the start and end time for each stop, ensuring a 30-minute buffer for transportation.
        Open Hours: List the open hours for each place.
        and so on for other two options at each preference.
        Format the output in a warm, conversational tone that feels like local advice for a fun day out, with no technical formatting or code. 
    """

    messages = [{"role": "user", "content": prompt}]

    stream = client.chat.completions.create(
        model="meta-llama/Meta-Llama-3-70B-Instruct", 
        messages=messages, 
        max_tokens=2048,
        temperature=temp,  # Dynamic temperature parameter
        stream=True,
    )

    outputs = ""
    for chunk in stream:
        outputs += chunk.choices[0].delta.content
    
    formatted_response = f"Here is the perference: {preferences} Here is the Prompt: {prompt}\nHere are some fun activities you might enjoy (Request ID: {request_id}):\n\n" + outputs

    logger.info(f"Request ID: {request_id}")
    logger.info(f"User Input: {user_input}")
    logger.info(f"Generated Response: {formatted_response[:200]}...")

    return formatted_response

@api_view(['POST'])
def chatbot_api(request):
    if request.method == 'POST':
        user_input = request.POST.get('message')
        timestamp = request.POST.get('timestamp', int(time.time()))  # Current timestamp if not provided
        request_id = str(uuid.uuid4())  # Unique request ID
        temp = request.POST.get('temperature', 0.7)  # Default temperature if not provided

        response = generate_planner_response(user_input, request_id, timestamp, temp)

        return HttpResponse(response) 

def call_model(request):
    if request.method == 'POST':
        message = request.POST.get('message')
        timestamp = request.POST.get('timestamp', int(time.time()))  # Current timestamp if not provided
        request_id = str(uuid.uuid4())  # Unique request ID
        temp = request.POST.get('temperature', 0.7)  # Default temperature if not provided

        output = generate_planner_response(message, request_id, timestamp, temp)

        return render(request, 'chatbot/output_page.html', {'output': output})
    
    return render(request, 'chatbot/input_page.html')