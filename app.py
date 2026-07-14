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

def generate_ai_summary(text_blob):
    try:
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        import json

        generation_config = genai.GenerationConfig(response_mime_type="application/json")
        model = genai.GenerativeModel('models/gemini-flash-latest', generation_config=generation_config)
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

        response_data = json.loads(response.text)
        return response_data

    except Exception as e:
        print(f"Gemini API error: {e}")
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
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        # --- Memory-reduction flags for low-RAM hosts (e.g. Render free tier) ---
        options.add_argument("--single-process")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-extensions")
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--lang=en-US,en")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        chrome_binary = os.getenv("CHROME_BIN")
        if chrome_binary:
            options.binary_location = chrome_binary

        chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
        try:
            service = Service(chromedriver_path) if chromedriver_path else Service(ChromeDriverManager().install())
        except Exception as e:
            print(f"Error installing chromedriver: {e}")
            print("This can sometimes be fixed by running: pip install --upgrade webdriver-manager")
            return jsonify({'error': 'Could not auto-install chromedriver.'}), 500

        driver = webdriver.Chrome(service=service, options=options)
        wait = WebDriverWait(driver, 15)
        stealth(
            driver,
            languages=["en-US", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
        )

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

        # Scroll down in steps to trigger Amazon's lazy-loaded reviews section.
        try:
            last_height = driver.execute_script("return document.body.scrollHeight")
            for _ in range(8):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.0)
                new_height = driver.execute_script("return document.body.scrollHeight")
                # Stop early once actual review elements are present
                if driver.find_elements(By.CSS_SELECTOR, '[data-hook="review"]'):
                    break
                if new_height == last_height:
                    break
                last_height = new_height
        except Exception:
            pass

        # Give the reviews a final chance to render after scrolling.
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '[data-hook="review"]')))
        except Exception:
            pass

        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        page_title = soup.title.text.strip() if soup.title else ""

        # --- Detect Amazon bot / CAPTCHA pages ---
        lowered = page_source.lower()
        bot_blocked = any(marker in lowered for marker in [
            "api-services-support@amazon",
            "type the characters you see",
            "enter the characters you see",
            "robot check",
            "to discuss automated access",
            "/errors/validatecaptcha",
        ])

        # --- 3. EXTRACT THE REVIEWS ---
        reviews_list = []
        review_elements = soup.select(
            'div[data-hook="review"], li[data-hook="review"], '
            'div[id^="customer_review"], div[id*="review-card"]'
        )

        # Amazon injects accessibility "teaser" text into the review body; strip it.
        noise_phrases = (
            "Brief content visible, double tap to read full content.",
            "Full content visible, double tap to read brief content.",
        )
        body_selectors = (
            '[data-hook="reviewText"], '
            'span[data-hook="review-body"], '
            'div[data-hook="review-collapsed"], '
            'span.review-text-content, '
            '.review-text-content'
        )
        for item in review_elements:
            body_element = item.select_one(body_selectors)
            review_body = ""
            if body_element:
                # Drop the collapsed/expanded accessibility teaser divs first
                for junk in body_element.select(
                    '.a-teaser-describedby-collapsed, .a-teaser-describedby-expanded'
                ):
                    junk.decompose()
                review_body = body_element.get_text(" ", strip=True)
                for noise in noise_phrases:
                    review_body = review_body.replace(noise, "").strip()
            if review_body:
                reviews_list.append(review_body)

        # If extraction failed, expose the first block's structure for debugging
        sample_structure = ""
        if not reviews_list and review_elements:
            first = review_elements[0]
            hooks = sorted({el.get("data-hook") for el in first.select("[data-hook]") if el.get("data-hook")})
            sample_structure = " data-hooks=" + ",".join(hooks)

        print(f"[DIAG] title={page_title!r} bot_blocked={bot_blocked} "
              f"review_blocks={len(review_elements)} reviews={len(reviews_list)}{sample_structure}")

        if not reviews_list:
            if bot_blocked:
                return jsonify({'error': 'Amazon blocked the request with a bot/CAPTCHA page. '
                                         'This is expected when scraping from a cloud server IP.'}), 400
            return jsonify({'error': f'Could not find any reviews. '
                                     f'(page title: "{page_title}", review blocks found: {len(review_elements)},'
                                     f'{sample_structure})'}), 400

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
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)