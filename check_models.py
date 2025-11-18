import google.generativeai as genai
import os
from dotenv import load_dotenv

# Load the .env file to get the key
load_dotenv()

print("--- Checking Gemini Models ---")

try:
    # Configure the API
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    print("API Key loaded. Fetching available models...\n")
    
    # This is the "Call ListModels" command
    # We will loop through all models and find the ones that can 'generateContent'
    
    found_models = False
    for model in genai.list_models():
        if 'generateContent' in model.supported_generation_methods:
            print(f"--- Found a USABLE model ---")
            print(f"Model name: {model.name}")
            print(f"Display name: {model.display_name}\n")
            found_models = True

    if not found_models:
        print("No models supporting 'generateContent' were found for your API key.")
    else:
        print("---")
        print("SUCCESS: Please copy one of the 'Model name' (e.g., 'models/gemini-1.5-flash') from the list above.")


except Exception as e:
    print(f"AN ERROR OCCURRED: {e}")
    print("Please double-check your API key in the .env file and your internet connection.")