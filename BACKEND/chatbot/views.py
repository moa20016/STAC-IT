from urllib import response
from django.shortcuts import render
import requests
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.http import HttpResponse
import time
import uuid
import logging
import re
from openai import OpenAI  # Import OpenAI SDK

# Initialize OpenAI client with DeepSeek API key
client = OpenAI(api_key="", base_url="https://api.deepseek.com/")

def generate_response(preference, options):
    response = f"Your preference: **{preference}**\nOptions:\n"
    for option in options:
        response += f"*{option}*\n"
    return response

TICKETMASTER_API_KEY = "" #Ticketmaster api key

logger = logging.getLogger(__name__)

def get_music_events(query, location=None):
    """Fetch music events from Ticketmaster API based on user query and location."""
    base_url = "https://app.ticketmaster.com/discovery/v2/events.json"
    
    city, state = (location.split(", ") if location else ("Stamford", "CT"))

    params = {
        "apikey": TICKETMASTER_API_KEY,
        "keyword": query,
        "classificationName": "Music",
        "sort": "date,asc",
        "size": 5,
    }

    if city:
        params["city"] = city
    if state:
        params["stateCode"] = state

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
                    "location": event.get("_embedded", {}).get("venues", [{}])[0].get("address", {}).get("line1", "Location not available"),
                    "city": event.get("_embedded", {}).get("venues", [{}])[0].get("city", {}).get("name", "City not available"),
                    "state": event.get("_embedded", {}).get("venues", [{}])[0].get("state", {}).get("stateCode", "State not available"),
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

api_key = ""  # Google Places API key

def get_place_details(place_id, api_key):
    base_url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "key": api_key,
        "fields": "name,formatted_address,rating,opening_hours,price_level,types" 
    }
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        return response.json().get("result", {})
    else:
        logger.error(f"Error fetching place details: {response.status_code} - {response.text}")
        return {}

def google_places_text_search(api_key, query, location=None, radius=None):
    location1, preferences, budget = parse_user_input(query)
    limit = 3 * len(preferences) + 3 ##can use limit if you want to limit the number of results returned
    
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
                detailed_place = get_place_details(place_id, api_key)
                detailed_results.append(detailed_place)
        return detailed_results
    else:
        logger.error(f"Error fetching text search: {response.status_code} - {response.text}")
        return []
    

def parse_user_input(user_input):
    """Extracts location, preferences, and budget from user input."""
    location_match = re.search(r"Location:\s*([^,]+,\s*[A-Z]{2})", user_input)
    preferences_match = re.search(r"Preferences:\s*(.*?)(?:,?\s*Budget:|$)", user_input)
    budget_match = re.search(r"Budget:\s*\$?(\d+)", user_input)

    location = location_match.group(1).strip() if location_match else None
    preferences_str = preferences_match.group(1).strip() if preferences_match else "music"
    preferences = [pref.strip() for pref in preferences_str.split(",")]
    budget = int(budget_match.group(1)) if budget_match else None

    return location, preferences, budget

def generate_planner_response(user_input, request_id, timestamp, temp):
    location, preferences, budget = parse_user_input(user_input)
    places_data = google_places_text_search(api_key, user_input)
    music_data = get_music_events(preferences, location)

    if isinstance(places_data, dict) and "error" in places_data:
        return places_data["error"]

    prompt = f"""
[Request ID: {request_id}]
[Timestamp: {timestamp}]

Help the user create a sequential day plan based on the following data:
- GOOGLE_DATA: {places_data}
- MUSIC_DATA: {music_data}
- USER_INPUT: '{user_input}'

### OBJECTIVE ###
Extract the user's preferences from their input and generate a plan with **non-overlapping, time-ordered activities** that fit within the user's available time window. Add a **30-minute buffer** between activities for transportation. Prioritize MUSIC_DATA for music or live events over GOOGLE_DATA. Ensure the places or events are open during the user's available hours.

### RULES ###
- Use MUSIC_DATA first for music or live events; fallback to GOOGLE_DATA for everything else.
- Suggest exactly 3 high-rated options per preference.
- Ensure NO two preferences or their options overlap in time.
- Ensure the plan is time-ordered and includes a 30-minute buffer between activities.
- If the total duration of all preferences (including 30-minute buffers) exceeds the user's available time window, EXTEND the end time slightly to fit the full plan.
- Respect each place’s open hours.

For each preference, provide the following details in a structured JSON format:
1. Preference: Label of the preference.
2. Options: A list of exactly three options, each containing:
   - Name: The name of the place or event.
   - Activity Description: A detailed description of what the user will enjoy, including recommended dishes, flavors, or highlights. Create an engaging experience.
   - Location: The address of the place or event.
   - Timing: The start and end time for the activity, including a 30-minute buffer for transportation.
   - Open Hours: The operating hours of the place or event.

Ensure the output is in the following JSON format and **ONLY OUTPUT THE JSON AND NOTHING ELSE**:
{{
  "request_id": "{request_id}",
  "timestamp": "{timestamp}",
  "preferences": [
    {{
      "preference": "Preference 1",
      "options": [
        {{
          "name": "Option 1 Name",
          "activity_description": "Detailed description of the experience.",
          "location": "Address of the location.",
          "timing": {{
            "start": "Start time with buffer",
            "end": "End time"
          }},
          "open_hours": "Operating hours of the place."
        }},
        {{
          "name": "Option 2 Name",
          "activity_description": "Detailed description of the experience.",
          "location": "Address of the location.",
          "timing": {{
            "start": "Start time with buffer",
            "end": "End time"
          }},
          "open_hours": "Operating hours of the place."
        }},
        {{
          "name": "Option 3 Name",
          "activity_description": "Detailed description of the experience.",
          "location": "Address of the location.",
          "timing": {{
            "start": "Start time with buffer",
            "end": "End time"
          }},
          "open_hours": "Operating hours of the place."
        }}
      ]
    }},
    {{
      "preference": "Preference 2",
      "options": [
        {{
          "name": "Option 1 Name",
          "activity_description": "Detailed description of the experience.",
          "location": "Address of the location.",
          "timing": {{
            "start": "Start time with buffer",
            "end": "End time"
          }},
          "open_hours": "Operating hours of the place."
        }},
        {{
          "name": "Option 2 Name",
          "activity_description": "Detailed description of the experience.",
          "location": "Address of the location.",
          "timing": {{
            "start": "Start time with buffer",
            "end": "End time"
          }},
          "open_hours": "Operating hours of the place."
        }},
        {{
          "name": "Option 3 Name",
          "activity_description": "Detailed description of the experience.",
          "location": "Address of the location.",
          "timing": {{
            "start": "Start time with buffer",
            "end": "End time"
          }},
          "open_hours": "Operating hours of the place."
        }}
      ]
    }}
    // Add more preferences as needed
  ]
}}
"""

    messages = [{"role": "user", "content": prompt}]

    # Use DeepSeek API instead of Hugging Face
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages,
        max_tokens=4096,
        temperature = temp, # Dynamic temperature parameter
        stream=False,
    )

    logger.info(f"Raw response: {response}")

    outputs = response.choices[0].message.content

    formatted_response = outputs

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
