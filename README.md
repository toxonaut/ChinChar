# ChinChar - Chinese Character Flashcard App

A web application for learning Chinese characters through flashcards. The app shows random Chinese characters and tracks your learning progress.

## Features

- Display random Chinese characters as flashcards
- Show character details (pinyin and meaning) when flipped
- Track learning progress with "Know", "Unsure", and "Don't Know" options
- Spaced repetition system - characters you know well appear less frequently

## Setup

1. Clone the repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run the application:
   ```
   python app.py
   ```

## Deployment

This application is designed to be deployed on Railway. To deploy:

1. Create a Railway account
2. Create a new project
3. Connect your GitHub repository
4. Railway will automatically detect the requirements.txt and deploy the app

## Tech Stack

- Python/Flask for backend
- SQLite for local development, PostgreSQL for production
- HTML/CSS/JavaScript for frontend
