import time
import random
import os
from dotenv import load_dotenv
import google.generativeai as genai

from flask import Flask, render_template, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
# --- THIS IMPORT WAS MISSING ---
from webdriver_manager.chrome import ChromeDriverManager 
from bs4 import BeautifulSoup
from selenium_stealth import stealth
from textblob import TextBlob
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# --- Load environment variables from .env file ---
load_dotenv()

# --- Configure the Gemini API ---
try:
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
except TypeError:
    print("ERROR: Could not find GEMINI_API_KEY. Make sure it's set in your .env file.")
    exit()

# Initialize the Flask App
app = Flask(__name__)

# --- NEW: GEMINI-BASED SUMMARY FUNCTION ---
def generate_ai_summary(text_blob):
    try:
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        # NEW: Import the JSON library at the top of app.py
        import json

        # ... (in your generate_ai_summary function)

        # NEW: Tell the model to output JSON
        generation_config = genai.GenerationConfig(response_mime_type="application/json")
        model = genai.GenerativeModel('models/gemini-flash-latest', generation_config=generation_config)
        # This is our prompt to the AI
        # This is our new, more advanced prompt
        prompt = f"""
        You are a product review summarizer. I will give you a block of raw product reviews.
        Your job is to read all of them and generate a response in a strict JSON format.

        The JSON object must have two keys:
        1. "verdict": A single, objective "Verdict" in 50 words or less.
        2. "pros": A short bullet-point list (as an array of strings) of the top 2-3 most mentioned positive points.
        3. "cons": A short bullet-point list (as an array of strings) of the top 2-3 most mentioned negative points.

        Here are the reviews:
        ---
        {text_blob}
        ---
        
        Example of your required output:
        {{
          "verdict": "This is a great product for its price, but suffers from poor battery life.",
          "pros": ["Easy to install", "Very bright light"],
          "cons": ["Battery dies quickly", "Feels a bit cheap"]
        }}
        """
        
        response = model.generate_content(prompt)

        # NEW: Parse the JSON response
        response_data = json.loads(response.text)
        return response_data # Return the whole Python dictionary

    except Exception as e:
        print(f"Gemini API error: {e}")
        # NEW: Return an error in the same JSON format
        return {
            "verdict": "Could not generate AI summary. The API may be down or the key invalid.",
            "pros": [],
            "cons": []
        }
        
    except Exception as e:
        print(f"Gemini API error: {e}")
        # Fallback if API fails
        return "Could not generate AI summary. The API may be down or the key invalid."


# Main route to serve the HTML page
@app.route('/')
def index():
    return render_template('index.html')

# Route to handle the analysis
@app.route('/analyze', methods=['POST'])
def analyze():
    driver = None
    try:
        url = request.form['product_url']

        # --- 1. SET UP THE SELENIUM SCRAPER ---
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        # --- NEW: Use ChromeDriverManager to auto-handle versions ---
        # It checks your browser version and downloads the matching driver.
        try:
            service = Service(ChromeDriverManager().install())
        except Exception as e:
            print(f"Error installing chromedriver: {e}")
            print("This can sometimes be fixed by running: pip install --upgrade webdriver-manager")
            return jsonify({'error': 'Could not auto-install chromedriver.'}), 500

        # --- ALL THE LINES BELOW WERE INDENTED INCORRECTLY ---
        # They are now correctly INSIDE the 'try' block
        
        driver = webdriver.Chrome(service=service, options=options)
        wait = WebDriverWait(driver, 15)
        #stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32")

        # --- 2. RUN THE SCRAPER ---
        driver.get(url)
        try:
            continue_button = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Continue shopping')]"))
            )
            time.sleep(random.uniform(1, 2))
            continue_button.click()
        except Exception:
            pass 

        wait.until(EC.visibility_of_element_located((By.ID, "reviewsMedley")))
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # --- 3. EXTRACT THE REVIEWS ---
        reviews_list = []
        review_elements = soup.select('div[id*="review-card"]')
        
        for item in review_elements:
            review_body_element = item.select_one('span[data-hook="review-body"], span.review-text-content')
            review_body = review_body_element.text.strip() if review_body_element else ""
            if review_body:
                reviews_list.append(review_body)
        
        if not reviews_list:
            return jsonify({'error': 'Could not find any reviews for this product.'}), 400

        # --- 4. RUN SENTIMENT & AI SUMMARY ---
        analyzer = SentimentIntensityAnalyzer()
        tb_scores = []
        vader_scores = []

        for review in reviews_list:
            tb_scores.append(TextBlob(review).sentiment.polarity)
            vader_scores.append(analyzer.polarity_scores(review)['compound'])

        avg_tb_score = sum(tb_scores) / len(tb_scores)
        avg_vader_score = sum(vader_scores) / len(vader_scores)

        if avg_vader_score > 0.05:
            interpretation = "Overall Positive"
        elif avg_vader_score < -0.05:
            interpretation = "Overall Negative"
        else:
            interpretation = "Overall Neutral"

        # --- NEW: Call the AI summary function ---
        all_reviews_text = " ".join(reviews_list)
        summary = generate_ai_summary(all_reviews_text) 

        # --- 5. SEND BACK THE RESULTS ---
        results = {
            'review_count': len(reviews_list),
            'avg_tb_score': f"{avg_tb_score:.2f}",
            'avg_vader_score': f"{avg_vader_score:.2f}",
            'interpretation': interpretation,
            'verdict': summary['verdict'],
            'pros': summary['pros'],
            'cons': summary['cons']
        }
        return jsonify(results)

    except Exception as e:
        return jsonify({'error': f"An error occurred: {str(e)}"}), 500

    finally:
        if driver:
            driver.quit()

# Run the app
if __name__ == '__main__':
    app.run(debug=True)