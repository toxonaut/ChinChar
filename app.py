import os
from flask import Flask, render_template, request, jsonify, make_response, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from models import db, Character, UserProgress, get_next_character, update_progress, User, CharacterAIDescription, UserCharacterTuning
import random
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import hashlib
import string
from authlib.integrations.flask_client import OAuth
import requests
import re as re_module
import jieba
from pycccedict.cccedict import CcCedict

# Initialize CC-CEDICT dictionary once at module level
_cccedict = CcCedict()

def _numbered_to_tonemarks(s: str) -> str:
    """Convert numbered pinyin like 'bei3 jing1' to tone marks like 'běi jīng'."""
    _tone_marks = {
        'a': 'āáǎà', 'e': 'ēéěè', 'i': 'īíǐì',
        'o': 'ōóǒò', 'u': 'ūúǔù', 'ü': 'ǖǘǚǜ',
    }
    def _convert_syllable(m):
        syllable = m.group(1).lower()
        tone = int(m.group(2))
        if tone == 5 or tone == 0:
            return syllable
        for v in ('a', 'e'):
            if v in syllable:
                return syllable.replace(v, _tone_marks[v][tone - 1])
        if 'ou' in syllable:
            return syllable.replace('o', _tone_marks['o'][tone - 1])
        for idx in range(len(syllable) - 1, -1, -1):
            ch = syllable[idx]
            if ch in _tone_marks:
                return syllable[:idx] + _tone_marks[ch][tone - 1] + syllable[idx + 1:]
        return syllable
    s = s.replace('v', 'ü')
    return re_module.sub(r'([a-züA-ZÜ]+)([0-5])', _convert_syllable, s)

def _is_chinese_token(s: str) -> bool:
    return any('\u4e00' <= ch <= '\u9fff' for ch in s)

# Load environment variables from .env file
load_dotenv()

# Import configuration from config.py
try:
    import config
    print("Successfully imported configuration from config.py")
except ImportError:
    print("Warning: config.py not found, using environment variables only")
    config = None

# Debug environment variables
print("Environment variables and configuration loaded:")
client_id = os.environ.get('GOOGLE_CLIENT_ID') or (config.GOOGLE_CLIENT_ID if config else None)
client_secret = os.environ.get('GOOGLE_CLIENT_SECRET') or (config.GOOGLE_CLIENT_SECRET if config else None)
print(f"GOOGLE_CLIENT_ID: {client_id[:10] + '...' if client_id else 'Not set'} (length: {len(client_id) if client_id else 0})")
print(f"GOOGLE_CLIENT_SECRET: {client_secret[:5] + '...' if client_secret else 'Not set'} (length: {len(client_secret) if client_secret else 0})")

# Determine if we're running in production
is_production = os.environ.get('RAILWAY_STATIC_URL') is not None
print(f"Running in production mode: {is_production}")

app = Flask(__name__)

# Configure database
_raw_db_url = os.environ.get('DATABASE_URL', '')
if _raw_db_url:
    print(f"DATABASE_URL is set (starts with: {_raw_db_url[:30]}...)")
else:
    print("WARNING: DATABASE_URL is NOT set, falling back to SQLite (data will be lost on redeploy!)")
    _raw_db_url = 'sqlite:///chinchar.db'

app.config['SQLALCHEMY_DATABASE_URI'] = _raw_db_url
# Replace postgres:// with postgresql:// in the DATABASE_URL (Railway specific)
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)
    print("Replaced postgres:// with postgresql:// in DATABASE_URL")
print(f"Final DB URI scheme: {app.config['SQLALCHEMY_DATABASE_URI'].split('://')[0]}://")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Set a secret key for session management
# IMPORTANT: SECRET_KEY must be stable across restarts, otherwise all sessions are invalidated.
# If SECRET_KEY env var is not set, derive a deterministic fallback from DATABASE_URL.
_secret = os.environ.get('SECRET_KEY')
if not _secret:
    _db_url = os.environ.get('DATABASE_URL', 'fallback-chinchar-secret')
    _secret = hashlib.sha256(_db_url.encode()).hexdigest()
    print('WARNING: SECRET_KEY not set, using deterministic fallback. Set SECRET_KEY in Railway env vars for best security.')
app.secret_key = _secret

# Configure session to be more robust
app.config['SESSION_COOKIE_SECURE'] = is_production  # Set to True in production with HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
app.config['REMEMBER_COOKIE_SECURE'] = is_production
app.config['REMEMBER_COOKIE_HTTPONLY'] = True

# Force HTTPS for external URLs when running on Railway
if is_production:
    app.config['PREFERRED_URL_SCHEME'] = 'https'
    print("Forcing HTTPS for external URLs in production mode")

# Initialize database
db.init_app(app)

# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize OAuth
oauth = OAuth(app)

# Helper function to get the correct redirect URI with HTTPS in production
def get_redirect_uri(endpoint):
    uri = url_for(endpoint, _external=True)
    if is_production and uri.startswith('http:'):
        uri = uri.replace('http:', 'https:', 1)
        print(f"Forced HTTPS for redirect URI: {uri}")
    return uri

# Configure Google OAuth with environment-specific settings
google_config = {
    'name': 'google',
    'client_id': client_id,
    'client_secret': client_secret,
    'access_token_url': 'https://oauth2.googleapis.com/token',
    'access_token_params': None,
    'authorize_url': 'https://accounts.google.com/o/oauth2/auth',
    'authorize_params': None,
    'api_base_url': 'https://www.googleapis.com/oauth2/v2/',
    'userinfo_endpoint': 'https://openidconnect.googleapis.com/v1/userinfo',
    'client_kwargs': {'scope': 'openid email profile'},
    'jwks_uri': 'https://www.googleapis.com/oauth2/v3/certs'
}

# Log the OAuth configuration (without sensitive data)
print(f"OAuth configuration: client_id exists: {bool(google_config['client_id'])}, "
      f"client_secret exists: {bool(google_config['client_secret'])}")

google = oauth.register(**google_config)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle login page"""
    error = request.args.get('error')
    
    if request.method == 'POST':
        email = request.form.get('email')
        
        if not email or '@' not in email:
            return render_template('login.html', error='Please enter a valid email address')
        
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        
        if not user:
            # Create new user
            print(f"Creating new user with email: {email}")
            user = User(
                email=email,
                name=email.split('@')[0],  # Use part before @ as name
                profile_pic=f"https://www.gravatar.com/avatar/{hashlib.md5(email.lower().encode()).hexdigest()}?d=identicon"
            )
            db.session.add(user)
            db.session.commit()
        else:
            # Update last login time
            print(f"User found, updating last login time for: {user.email}")
            user.last_login = datetime.utcnow()
            db.session.commit()
        
        # Log in the user (remember=True keeps session for 30 days)
        login_user(user, remember=True)
        print(f"User logged in successfully: {user.email}")
        
        # Redirect to home page
        return redirect(url_for('index'))
    
    return render_template('login.html', error=error)

@app.route('/login/google')
def google_login():
    """Initiate Google OAuth login flow"""
    # Check if credentials are configured
    if not os.environ.get('GOOGLE_CLIENT_ID'):
        print("ERROR: Google OAuth client ID is missing or empty!")
        return render_template('login.html', error='Google login is not properly configured (missing client ID). Please contact the administrator.')
    
    if not os.environ.get('GOOGLE_CLIENT_SECRET'):
        print("ERROR: Google OAuth client secret is missing or empty!")
        return render_template('login.html', error='Google login is not properly configured (missing client secret). Please contact the administrator.')
    
    redirect_uri = get_redirect_uri('google_auth')
    print(f"Google OAuth redirect URI: {redirect_uri}")
    print(f"Request host: {request.host}")
    print(f"Request scheme: {request.scheme}")
    print(f"Full request URL: {request.url}")
    
    # Add more detailed debugging for Railway environment
    print(f"Environment variables for debugging:")
    print(f"RAILWAY_STATIC_URL: {os.environ.get('RAILWAY_STATIC_URL')}")
    print(f"PORT: {os.environ.get('PORT')}")
    print(f"HOST: {os.environ.get('HOST')}")
    
    try:
        return google.authorize_redirect(redirect_uri)
    except Exception as e:
        print(f"Error during Google OAuth redirect: {e}")
        return render_template('login.html', error=f'Error initiating Google login: {str(e)}')

@app.route('/login/google/callback')
def google_auth():
    """Handle Google OAuth callback"""
    try:
        # Log the request details for debugging
        print(f"Google OAuth callback received - Host: {request.host}, Path: {request.path}")
       
        # Get the token with more detailed error handling
        try:
            token = google.authorize_access_token()
            print("Successfully obtained access token")
        except Exception as token_error:
            print(f"Error obtaining access token: {token_error}")
            return render_template('login.html', error=f'Authentication failed: Error obtaining access token. Please try again or use email login.')
        
        # Get user info directly from userinfo endpoint with more detailed error handling
        try:
            userinfo_response = google.get('userinfo')
            print(f"Userinfo response status: {userinfo_response.status_code}")
            
            if userinfo_response.status_code != 200:
                error_text = userinfo_response.text
                print(f"Failed to get user info: {error_text}")
                return render_template('login.html', error=f'Failed to get user info: {error_text}. Please try again or use email login.')
        except Exception as userinfo_error:
            print(f"Exception during userinfo request: {userinfo_error}")
            return render_template('login.html', error=f'Error getting user info: {str(userinfo_error)}. Please try again or use email login.')
            
        # Parse the user info with error handling
        try:
            user_info = userinfo_response.json()
            print(f"Google OAuth callback - User info received: {user_info.get('email')}")
        except Exception as json_error:
            print(f"Error parsing user info JSON: {json_error}")
            print(f"Raw response: {userinfo_response.text[:200]}")  # Print first 200 chars to avoid huge logs
            return render_template('login.html', error=f'Error parsing user data. Please try again or use email login.')
        
        # Check required fields
        if not user_info.get('email'):
            print("Email not provided by Google")
            return render_template('login.html', error='Email not provided by Google. Please try again or use email login.')
        
        # Check if user exists by Google ID
        google_id = user_info.get('id') or user_info.get('sub')  # 'sub' is used in OpenID Connect
        if not google_id:
            print("No user ID (id or sub) provided by Google")
            return render_template('login.html', error='User ID not provided by Google. Please try again or use email login.')
            
        # Database operations with error handling
        try:
            user = User.query.filter_by(google_id=google_id).first()
            
            if not user:
                # Check if user exists by email
                user = User.query.filter_by(email=user_info['email']).first()
                
                if user:
                    # Update existing user with Google ID
                    user.google_id = google_id
                    user.profile_pic = user_info.get('picture')
                    if not user.name:
                        user.name = user_info.get('name')
                    print(f"Updated existing user with Google ID: {user.email}")
                else:
                    # Create new user
                    user = User(
                        google_id=google_id,
                        email=user_info['email'],
                        name=user_info.get('name'),
                        profile_pic=user_info.get('picture')
                    )
                    db.session.add(user)
                    print(f"Created new user from Google login: {user_info['email']}")
            
            # Update last login time
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            # Log in the user (remember=True keeps session for 30 days)
            login_user(user, remember=True)
            print(f"User logged in via Google: {user.email}")
            
            # Redirect to home page
            return redirect(url_for('index'))
        except Exception as db_error:
            db.session.rollback()
            print(f"Database error during Google authentication: {db_error}")
            return render_template('login.html', error=f'Database error during authentication. Please try again or use email login.')
    except Exception as e:
        print(f"Unhandled error during Google authentication: {e}")
        error_details = str(e)
        return render_template('login.html', error=f'Authentication failed: {error_details}. Please try again or use email login.')

@app.route('/logout')
@login_required
def logout():
    """Log out the user"""
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    """Render the main page"""
    return render_template('index.html')

@app.route('/known')
@login_required
def known_characters():
    """Render the page showing known characters"""
    # Get all characters with familiarity level 2 (Know)
    progress = UserProgress.query.filter_by(user_id=current_user.id, familiarity=2).all()
    characters = []
    
    for p in progress:
        character = Character.query.get(p.character_id)
        if character:
            characters.append({
                'id': character.id,
                'hanzi': character.hanzi,
                'pinyin': character.pinyin,
                'meaning': character.meaning,
                'last_reviewed': p.last_reviewed,
                'review_count': p.review_count,
                'know_count': p.know_count,
                'unsure_count': p.unsure_count,
                'dont_know_count': p.dont_know_count
            })
    
    # Sort by dont_know_count and unsure_count (descending), then by last_reviewed
    characters.sort(key=lambda x: (-(x['dont_know_count'] + x['unsure_count']), -x['last_reviewed'].timestamp()))
    
    return render_template('character_list.html', 
                          title='Characters You Know', 
                          characters=characters, 
                          category='known')

@app.route('/unsure')
@login_required
def unsure_characters():
    """Render the page showing unsure characters"""
    # Get all characters with familiarity level 1 (Unsure)
    progress = UserProgress.query.filter_by(user_id=current_user.id, familiarity=1).all()
    characters = []
    
    for p in progress:
        character = Character.query.get(p.character_id)
        if character:
            characters.append({
                'id': character.id,
                'hanzi': character.hanzi,
                'pinyin': character.pinyin,
                'meaning': character.meaning,
                'last_reviewed': p.last_reviewed,
                'review_count': p.review_count,
                'know_count': p.know_count,
                'unsure_count': p.unsure_count,
                'dont_know_count': p.dont_know_count
            })
    
    # Sort by unsure_count (descending), then by last_reviewed
    characters.sort(key=lambda x: (-x['unsure_count'], -x['last_reviewed'].timestamp()))
    
    return render_template('character_list.html', 
                          title='Characters You\'re Unsure About', 
                          characters=characters, 
                          category='unsure')

@app.route('/unknown')
@login_required
def unknown_characters():
    """Render the page showing unknown characters"""
    # Get all characters with familiarity level 0 (Don't Know)
    progress = UserProgress.query.filter_by(user_id=current_user.id, familiarity=0).all()
    characters = []
    
    for p in progress:
        character = Character.query.get(p.character_id)
        if character:
            characters.append({
                'id': character.id,
                'hanzi': character.hanzi,
                'pinyin': character.pinyin,
                'meaning': character.meaning,
                'last_reviewed': p.last_reviewed,
                'review_count': p.review_count,
                'know_count': p.know_count,
                'unsure_count': p.unsure_count,
                'dont_know_count': p.dont_know_count
            })
    
    # Sort by dont_know_count (descending), then by last_reviewed
    characters.sort(key=lambda x: (-x['dont_know_count'], -x['last_reviewed'].timestamp()))
    
    return render_template('character_list.html', 
                          title='Characters You Don\'t Know', 
                          characters=characters, 
                          category='unknown')

@app.route('/api/character/next', methods=['GET'])
@login_required
def next_character():
    """Get the next character to review"""
    try:
        # Get the user_id from current_user
        user_id = current_user.id
        
        # Get the next character for this user
        character = get_next_character(user_id)
        
        if not character:
            # If no character is found, return a default or error
            return jsonify({'error': 'No characters available'}), 404
        
        return jsonify({
            'id': character.id,
            'hanzi': character.hanzi
        })
    except Exception as e:
        app.logger.error(f"Error in next_character: {e}")
        return jsonify({'error': 'An error occurred while retrieving the next character'}), 500

@app.route('/api/character/<int:character_id>', methods=['GET'])
@login_required
def get_character(character_id):
    """Get details for a specific character"""
    try:
        character = Character.query.get(character_id)
        
        if not character:
            return jsonify({'error': 'Character not found'}), 404
        
        # Debug output to see what's being returned
        print(f"Character details: ID={character.id}, Hanzi='{character.hanzi}', Pinyin='{character.pinyin}', Meaning='{character.meaning}'")
        
        return jsonify({
            'id': character.id,
            'hanzi': character.hanzi,
            'pinyin': character.pinyin,
            'meaning': character.meaning
        })
    except Exception as e:
        app.logger.error(f"Error getting character: {e}")
        return jsonify({'error': 'An error occurred while retrieving the character'}), 500

@app.route('/api/character/<int:character_id>/ai-description', methods=['GET'])
@login_required
def get_ai_description(character_id):
    try:
        character = Character.query.get(character_id)
        if not character:
            return jsonify({'error': 'Character not found'}), 404

        existing = CharacterAIDescription.query.filter_by(character_id=character_id).first()
        if existing:
            return jsonify({'character_id': character_id, 'content': existing.content, 'cached': True})

        api_key = os.environ.get('api_key') or os.environ.get('API_KEY')
        if not api_key:
            app.logger.error("AI description: No api_key or API_KEY env var found")
            return jsonify({'error': 'Missing API key. Set api_key or API_KEY in environment variables.'}), 500

        app.logger.info(f"AI description: Using API key starting with {api_key[:8]}...")

        system_prompt = 'You come up with example words and sentences for a chinese dictionary app. Just show the answers for use in a dictionary app. no "of course" etc. Start the answers with a short description of the character, then examples.'
        user_prompt = f'show the most common words using the character {character.hanzi} including example sentences'

        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'gpt-4.1',
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt}
                ],
                'temperature': 0.7,
                'max_tokens': 800
            },
            timeout=30
        )

        if response.status_code != 200:
            try:
                error_detail = response.json().get('error', {}).get('message', response.text[:200])
            except Exception:
                error_detail = response.text[:200]
            app.logger.error(f"OpenAI error {response.status_code}: {error_detail}")
            return jsonify({'error': f'OpenAI API error ({response.status_code}): {error_detail}'}), 502

        payload = response.json()
        content = payload['choices'][0]['message']['content']

        record = CharacterAIDescription(character_id=character_id, content=content, model='gpt-4.1')
        db.session.add(record)
        db.session.commit()

        return jsonify({'character_id': character_id, 'content': content, 'cached': False})
    except Exception as e:
        app.logger.error(f"Error getting AI description: {e}")
        db.session.rollback()
        return jsonify({'error': 'An error occurred while generating AI description'}), 500

@app.route('/api/character/demote', methods=['POST'])
@login_required
def demote_character():
    """Increase per-user rank penalty so the character is shown less often"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        character_id = data.get('character_id')
        if not character_id:
            return jsonify({'error': 'No character_id provided'}), 400

        character = Character.query.get(character_id)
        if not character:
            return jsonify({'error': 'Character not found'}), 404

        tuning = UserCharacterTuning.query.filter_by(
            user_id=current_user.id,
            character_id=character_id
        ).first()

        if not tuning:
            tuning = UserCharacterTuning(user_id=current_user.id, character_id=character_id, rank_penalty=0)
            db.session.add(tuning)

        tuning.rank_penalty += 50
        db.session.commit()

        return jsonify({'success': True, 'rank_penalty': tuning.rank_penalty})
    except Exception as e:
        app.logger.error(f"Error demoting character: {e}")
        db.session.rollback()
        return jsonify({'error': 'An error occurred while updating character tuning'}), 500

@app.route('/api/progress', methods=['POST'])
@login_required
def update_user_progress():
    """Update the user's progress for a character"""
    try:
        print("=== Update Progress Request ===")
        data = request.get_json()
        print(f"Request data: {data}")
        
        if not data:
            print("Error: No data provided in request")
            return jsonify({'error': 'No data provided'}), 400
            
        character_id = data.get('character_id')
        familiarity = data.get('familiarity')
        
        print(f"Character ID: {character_id}, Familiarity: {familiarity}")
        
        if not character_id:
            print("Error: No character_id provided in request")
            return jsonify({'error': 'No character_id provided'}), 400
        
        if familiarity is None:
            print("Error: No familiarity provided in request")
            return jsonify({'error': 'No familiarity provided'}), 400
        
        # Validate familiarity
        if familiarity not in [0, 1, 2]:
            print(f"Error: Invalid familiarity value: {familiarity}")
            return jsonify({'error': 'Invalid familiarity value'}), 400
        
        # Get user_id from current_user
        user_id = current_user.id
        print(f"User ID: {user_id}")
        
        # Update progress
        success = update_progress(user_id, character_id, familiarity)
        
        if not success:
            print(f"Error: Failed to update progress for character_id={character_id}")
            return jsonify({'error': 'Failed to update progress'}), 500
        
        print("Success: Progress updated successfully")
        return jsonify({'success': True})
    except Exception as e:
        import traceback
        print(f"Error in update_user_progress: {e}")
        traceback.print_exc()
        return jsonify({'error': 'An error occurred while updating progress'}), 500

@app.route('/api/bulk-import', methods=['POST'])
@login_required
def bulk_import_characters():
    """Bulk import characters from text"""
    try:
        data = request.get_json()
        
        if not data or 'characters' not in data:
            return jsonify({'error': 'No characters provided'}), 400
        
        characters = data['characters']
        
        # Get familiarity level (default to 2 - "Know")
        familiarity = data.get('familiarity', 2)
        if familiarity not in [0, 1, 2]:
            familiarity = 2  # Default to "Know" if invalid value
        
        # Get user_id from current_user
        user_id = current_user.id
        
        # Process each character
        results = {
            'success': 0,
            'failed': 0,
            'not_found': 0,
            'details': []
        }
        
        # Extract individual characters from the text
        unique_chars = []
        for char in characters:
            if char.strip() and not char.isspace() and not char.isdigit() and not char in string.punctuation:
                unique_chars.append(char)
        
        # Remove duplicates
        unique_chars = list(set(unique_chars))
        
        for char in unique_chars:
            # Find the character in the database
            character = Character.query.filter_by(hanzi=char).first()
            
            if not character:
                results['not_found'] += 1
                results['details'].append({
                    'character': char,
                    'status': 'not_found'
                })
                continue
            
            # Update progress with the specified familiarity level
            success = update_progress(user_id, character.id, familiarity)
            
            if success:
                results['success'] += 1
                results['details'].append({
                    'character': char,
                    'status': 'success'
                })
            else:
                results['failed'] += 1
                results['details'].append({
                    'character': char,
                    'status': 'failed'
                })
        
        return jsonify({
            'success': True,
            'message': f"Successfully imported {results['success']} characters. {results['not_found']} not found, {results['failed']} failed.",
            'results': results
        })
    except Exception as e:
        app.logger.error(f"Error in bulk import: {e}")
        return jsonify({'error': 'An error occurred during bulk import'}), 500

@app.route('/api/export-progress')
@login_required
def export_character_progress():
    """Export character progress as a JSON file including all states"""
    try:
        # Get all characters that have been reviewed
        progress = UserProgress.query.filter_by(user_id=current_user.id).all()

        tuning_records = UserCharacterTuning.query.filter_by(user_id=current_user.id).all()
        tuning_by_character_id = {t.character_id: t.rank_penalty for t in tuning_records}
        
        # Create a dictionary to store character progress
        progress_data = {
            "know": [],
            "unsure": [],
            "dont_know": [],
            "detailed": {},  # New section for detailed progress info
            "tuning": {}
        }
        
        for p in progress:
            character = Character.query.get(p.character_id)
            if character:
                # Add to the appropriate list based on familiarity
                if p.familiarity == 2:  # Know
                    progress_data["know"].append(character.hanzi)
                elif p.familiarity == 1:  # Unsure
                    progress_data["unsure"].append(character.hanzi)
                elif p.familiarity == 0:  # Don't Know
                    progress_data["dont_know"].append(character.hanzi)
                
                # Add detailed progress information
                progress_data["detailed"][character.hanzi] = {
                    "familiarity": p.familiarity,
                    "review_count": p.review_count,
                    "know_count": p.know_count,
                    "unsure_count": p.unsure_count,
                    "dont_know_count": p.dont_know_count,
                    "last_reviewed": p.last_reviewed.isoformat(),
                    "rank_penalty": tuning_by_character_id.get(character.id, 0)
                }

        for t in tuning_records:
            character = Character.query.get(t.character_id)
            if character:
                progress_data["tuning"][character.hanzi] = {
                    "rank_penalty": t.rank_penalty
                }
        
        # Convert to JSON
        progress_json = json.dumps(progress_data, ensure_ascii=False, indent=2)
        
        # Create a response with the JSON file
        response = make_response(progress_json)
        response.headers['Content-Disposition'] = 'attachment; filename=character_progress.json'
        response.headers['Content-Type'] = 'application/json; charset=utf-8'
        
        return response
    except Exception as e:
        app.logger.error(f"Error exporting character progress: {e}")
        return jsonify({'error': 'An error occurred while exporting character progress'}), 500

@app.route('/api/export-known')
@login_required
def export_known_characters():
    """Export known characters as a text file"""
    try:
        # Get all characters with familiarity level 2 (Know)
        progress = UserProgress.query.filter_by(user_id=current_user.id, familiarity=2).all()
        characters = []
        
        for p in progress:
            character = Character.query.get(p.character_id)
            if character:
                characters.append(character.hanzi)
        
        # Sort characters by frequency (most common first)
        characters_with_rank = []
        for char in characters:
            character = Character.query.filter_by(hanzi=char).first()
            if character:
                characters_with_rank.append((char, character.rank))
        
        # Sort by rank (ascending)
        characters_with_rank.sort(key=lambda x: x[1])
        
        # Extract just the characters
        sorted_characters = [char for char, _ in characters_with_rank]
        
        # Create a text file with the characters
        characters_text = ''.join(sorted_characters)
        
        # Create a response with the text file
        response = make_response(characters_text)
        response.headers['Content-Disposition'] = 'attachment; filename=known_characters.txt'
        response.headers['Content-Type'] = 'text/plain; charset=utf-8'
        
        return response
    except Exception as e:
        app.logger.error(f"Error exporting known characters: {e}")
        return jsonify({'error': 'An error occurred while exporting known characters'}), 500

@app.route('/api/import-progress', methods=['POST'])
@login_required
def import_character_progress():
    """Import character progress from a JSON file including all states"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part'}), 400
            
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
            
        if file:
            # Read the file content
            content = file.read().decode('utf-8')
            
            # Parse JSON
            try:
                progress_data = json.loads(content)
            except json.JSONDecodeError:
                return jsonify({'error': 'Invalid JSON format'}), 400
            
            # Validate format
            if not isinstance(progress_data, dict):
                return jsonify({'error': 'Invalid progress data format'}), 400
            
            # Get user_id from current_user
            user_id = current_user.id

            def apply_tuning_if_present():
                tuning_data = progress_data.get("tuning")
                if not tuning_data or not isinstance(tuning_data, dict):
                    return

                for hanzi, tuning in tuning_data.items():
                    character = Character.query.filter_by(hanzi=hanzi).first()
                    if not character:
                        continue

                    if isinstance(tuning, dict):
                        rank_penalty = tuning.get("rank_penalty", 0)
                    else:
                        rank_penalty = tuning

                    try:
                        rank_penalty = int(rank_penalty)
                    except (TypeError, ValueError):
                        rank_penalty = 0

                    record = UserCharacterTuning.query.filter_by(user_id=user_id, character_id=character.id).first()
                    if not record:
                        record = UserCharacterTuning(user_id=user_id, character_id=character.id, rank_penalty=0)
                        db.session.add(record)

                    record.rank_penalty = rank_penalty
            
            # Process results
            results = {
                'success': 0,
                'failed': 0,
                'not_found': 0,
                'know': 0,
                'unsure': 0,
                'dont_know': 0,
                'details': []
            }
            
            # Process detailed progress if available
            if "detailed" in progress_data and isinstance(progress_data["detailed"], dict):
                for hanzi, details in progress_data["detailed"].items():
                    character = Character.query.filter_by(hanzi=hanzi).first()
                    if not character:
                        results['not_found'] += 1
                        results['details'].append({
                            'character': hanzi,
                            'status': 'not_found'
                        })
                        continue
                    
                    try:
                        # Find existing progress record or create a new one
                        progress = UserProgress.query.filter_by(user_id=user_id, character_id=character.id).first()
                        
                        if not progress:
                            progress = UserProgress(
                                user_id=user_id,
                                character_id=character.id
                            )
                            db.session.add(progress)
                        
                        # Update with detailed information
                        progress.familiarity = details.get("familiarity", 0)
                        progress.review_count = details.get("review_count", 0)
                        progress.know_count = details.get("know_count", 0)
                        progress.unsure_count = details.get("unsure_count", 0)
                        progress.dont_know_count = details.get("dont_know_count", 0)
                        
                        # Parse last_reviewed if available
                        if "last_reviewed" in details:
                            try:
                                progress.last_reviewed = datetime.fromisoformat(details["last_reviewed"])
                            except ValueError:
                                progress.last_reviewed = datetime.utcnow()
                        else:
                            progress.last_reviewed = datetime.utcnow()
                        
                        results['success'] += 1
                        
                        # Increment the appropriate category counter
                        if progress.familiarity == 2:
                            results['know'] += 1
                        elif progress.familiarity == 1:
                            results['unsure'] += 1
                        elif progress.familiarity == 0:
                            results['dont_know'] += 1
                            
                        results['details'].append({
                            'character': hanzi,
                            'status': 'success',
                            'familiarity': progress.familiarity
                        })
                    except Exception as e:
                        app.logger.error(f"Error importing detailed progress for {hanzi}: {e}")
                        results['failed'] += 1
                        results['details'].append({
                            'character': hanzi,
                            'status': 'failed'
                        })

                apply_tuning_if_present()
                db.session.commit()
                
                # If detailed progress was processed, return early
                return jsonify({
                    'success': True,
                    'message': f"Successfully imported {results['success']} characters ({results['know']} known, {results['unsure']} unsure, {results['dont_know']} don't know). {results['not_found']} not found, {results['failed']} failed.",
                    'results': results
                })
            
            # Fall back to processing simple lists if no detailed information
            # Process "know" characters (familiarity = 2)
            if "know" in progress_data and isinstance(progress_data["know"], list):
                for char in progress_data["know"]:
                    character = Character.query.filter_by(hanzi=char).first()
                    if not character:
                        results['not_found'] += 1
                        results['details'].append({
                            'character': char,
                            'status': 'not_found'
                        })
                        continue
                    
                    success = update_progress(user_id, character.id, 2)
                    if success:
                        results['success'] += 1
                        results['know'] += 1
                        results['details'].append({
                            'character': char,
                            'status': 'success',
                            'familiarity': 'know'
                        })
                    else:
                        results['failed'] += 1
                        results['details'].append({
                            'character': char,
                            'status': 'failed'
                        })
            
            # Process "unsure" characters (familiarity = 1)
            if "unsure" in progress_data and isinstance(progress_data["unsure"], list):
                for char in progress_data["unsure"]:
                    character = Character.query.filter_by(hanzi=char).first()
                    if not character:
                        results['not_found'] += 1
                        results['details'].append({
                            'character': char,
                            'status': 'not_found'
                        })
                        continue
                    
                    success = update_progress(user_id, character.id, 1)
                    if success:
                        results['success'] += 1
                        results['unsure'] += 1
                        results['details'].append({
                            'character': char,
                            'status': 'success',
                            'familiarity': 'unsure'
                        })
                    else:
                        results['failed'] += 1
                        results['details'].append({
                            'character': char,
                            'status': 'failed'
                        })
            
            # Process "dont_know" characters (familiarity = 0)
            if "dont_know" in progress_data and isinstance(progress_data["dont_know"], list):
                for char in progress_data["dont_know"]:
                    character = Character.query.filter_by(hanzi=char).first()
                    if not character:
                        results['not_found'] += 1
                        results['details'].append({
                            'character': char,
                            'status': 'not_found'
                        })
                        continue
                    
                    success = update_progress(user_id, character.id, 0)
                    if success:
                        results['success'] += 1
                        results['dont_know'] += 1
                        results['details'].append({
                            'character': char,
                            'status': 'success',
                            'familiarity': 'dont_know'
                        })
                    else:
                        results['failed'] += 1
                        results['details'].append({
                            'character': char,
                            'status': 'failed'
                        })

            apply_tuning_if_present()
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': f"Successfully imported {results['success']} characters ({results['know']} known, {results['unsure']} unsure, {results['dont_know']} don't know). {results['not_found']} not found, {results['failed']} failed.",
                'results': results
            })
    except Exception as e:
        app.logger.error(f"Error importing character progress: {e}")
        return jsonify({'error': 'An error occurred during progress import'}), 500

@app.route('/api/import-file', methods=['POST'])
@login_required
def import_characters_from_file():
    """Import characters from an uploaded text file"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part'}), 400
            
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
            
        if file:
            # Read the file content
            content = file.read().decode('utf-8')
            
            # Get user_id from current_user
            user_id = current_user.id
            
            # Process each character
            results = {
                'success': 0,
                'failed': 0,
                'not_found': 0,
                'details': []
            }
            
            # Extract individual characters from the text
            characters = []
            for char in content:
                if char.strip() and not char.isspace():
                    characters.append(char)
            
            # Remove duplicates
            unique_characters = list(set(characters))
            
            for char in unique_characters:
                # Find the character in the database
                character = Character.query.filter_by(hanzi=char).first()
                
                if not character:
                    results['not_found'] += 1
                    results['details'].append({
                        'character': char,
                        'status': 'not_found'
                    })
                    continue
                
                # Mark as known (familiarity = 2)
                success = update_progress(user_id, character.id, 2)
                
                if success:
                    results['success'] += 1
                    results['details'].append({
                        'character': char,
                        'status': 'success'
                    })
                else:
                    results['failed'] += 1
                    results['details'].append({
                        'character': char,
                        'status': 'failed'
                    })
            
            return jsonify({
                'success': True,
                'message': f"Successfully imported {results['success']} characters. {results['not_found']} not found, {results['failed']} failed.",
                'results': results
            })
    except Exception as e:
        app.logger.error(f"Error importing characters from file: {e}")
        return jsonify({'error': 'An error occurred during file import'}), 500

@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    """Get user statistics"""
    total_characters = Character.query.count()
    reviewed_characters = UserProgress.query.filter_by(user_id=current_user.id).count()
    
    know_count = UserProgress.query.filter_by(user_id=current_user.id, familiarity=2).count()
    unsure_count = UserProgress.query.filter_by(user_id=current_user.id, familiarity=1).count()
    dont_know_count = UserProgress.query.filter_by(user_id=current_user.id, familiarity=0).count()
    
    return jsonify({
        'total_characters': total_characters,
        'reviewed_characters': reviewed_characters,
        'know_count': know_count,
        'unsure_count': unsure_count,
        'dont_know_count': dont_know_count
    })

@app.route('/import-export')
@login_required
def import_export_page():
    """Render the import/export page"""
    return render_template('import_export.html')

@app.route('/text-learner')
@login_required
def text_learner_page():
    """Render the text learner page"""
    return render_template('text_learner.html')

@app.route('/api/grammar-analysis', methods=['POST'])
@login_required
def grammar_analysis():
    """Send Chinese text to GPT-4.1 for sentence-by-sentence grammar analysis."""
    try:
        data = request.get_json()
        if not data or not data.get('text', '').strip():
            return jsonify({'error': 'Please enter some Chinese text'}), 400

        text = data['text'].strip()

        api_key = os.environ.get('api_key') or os.environ.get('API_KEY')
        if not api_key:
            return jsonify({'error': 'Missing API key. Set api_key or API_KEY in environment variables.'}), 500

        system_prompt = (
            'You are a Chinese language teacher. You break down Chinese text for learners. '
            'For the given text, split it into natural sentence chunks (a sentence or meaningful sentence fragment per chunk). '
            'For each chunk output EXACTLY this format:\n'
            'CHUNK: [the Chinese sentence/fragment exactly as given]\n'
            'EXPLANATION: [grammar explanation of that chunk]\n\n'
            'Rules:\n'
            '- Every word/phrase in the chunk must be explained with pinyin (using tone marks like ā á ǎ à, NOT numbers) and English meaning.\n'
            '- Explain grammar patterns used (e.g. 得-complement, 把-construction, 了 aspect marker, etc.).\n'
            '- Keep the original text exactly, do not modify or skip any part.\n'
            '- Each CHUNK/EXPLANATION pair must be separated by a blank line.\n'
            '- Do not add any other text, headers, or numbering outside of this format.'
        )

        user_prompt = f'Analyze this Chinese text:\n\n{text}'

        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'gpt-4.1',
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt}
                ],
                'temperature': 0.4,
                'max_tokens': 3000
            },
            timeout=60
        )

        if response.status_code != 200:
            try:
                error_detail = response.json().get('error', {}).get('message', response.text[:200])
            except Exception:
                error_detail = response.text[:200]
            app.logger.error(f"OpenAI grammar error {response.status_code}: {error_detail}")
            return jsonify({'error': f'OpenAI API error ({response.status_code}): {error_detail}'}), 502

        payload = response.json()
        content = payload['choices'][0]['message']['content']

        # Parse the CHUNK: / EXPLANATION: pairs
        chunks = []
        current_chunk = None
        current_explanation = None
        collecting = None

        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('CHUNK:'):
                # Save previous pair if exists
                if current_chunk is not None:
                    chunks.append({'sentence': current_chunk.strip(), 'explanation': (current_explanation or '').strip()})
                current_chunk = stripped[len('CHUNK:'):].strip()
                current_explanation = None
                collecting = 'chunk'
            elif stripped.startswith('EXPLANATION:'):
                current_explanation = stripped[len('EXPLANATION:'):].strip()
                collecting = 'explanation'
            elif collecting == 'explanation' and stripped:
                current_explanation = (current_explanation or '') + '\n' + stripped
            elif collecting == 'chunk' and stripped:
                current_chunk = (current_chunk or '') + stripped

        # Don't forget the last pair
        if current_chunk is not None:
            chunks.append({'sentence': current_chunk.strip(), 'explanation': (current_explanation or '').strip()})

        return jsonify({'chunks': chunks})
    except Exception as e:
        app.logger.error(f"Error in grammar analysis: {e}")
        return jsonify({'error': 'An error occurred during grammar analysis'}), 500

@app.route('/api/annotate-text', methods=['POST'])
@login_required
def annotate_text():
    """Tokenize Chinese text with jieba and look up each token in CC-CEDICT."""
    try:
        data = request.get_json()
        if not data or not data.get('text', '').strip():
            return jsonify({'error': 'Please enter some Chinese text'}), 400

        text = data['text'].strip()
        tokens = list(jieba.cut(text))
        result = []

        for tok in tokens:
            if not _is_chinese_token(tok):
                result.append({'token': tok, 'type': 'punctuation'})
                continue

            entry = _cccedict.get_entry(tok)
            if entry:
                pinyin_raw = entry.get('pinyin', '')
                pinyin = _numbered_to_tonemarks(pinyin_raw) if pinyin_raw else ''
                defs = entry.get('definitions', [])
                result.append({
                    'token': tok,
                    'type': 'chinese',
                    'pinyin': pinyin,
                    'definitions': defs
                })
            else:
                # No entry for the whole token — split into individual characters
                if len(tok) > 1:
                    for ch in tok:
                        if not _is_chinese_token(ch):
                            result.append({'token': ch, 'type': 'punctuation'})
                            continue
                        ch_entry = _cccedict.get_entry(ch)
                        if ch_entry:
                            ch_pinyin_raw = ch_entry.get('pinyin', '')
                            ch_pinyin = _numbered_to_tonemarks(ch_pinyin_raw) if ch_pinyin_raw else ''
                            result.append({
                                'token': ch,
                                'type': 'chinese',
                                'pinyin': ch_pinyin,
                                'definitions': ch_entry.get('definitions', [])
                            })
                        else:
                            result.append({
                                'token': ch,
                                'type': 'chinese',
                                'pinyin': '',
                                'definitions': []
                            })
                else:
                    result.append({
                        'token': tok,
                        'type': 'chinese',
                        'pinyin': '',
                        'definitions': []
                    })

        return jsonify({'tokens': result})
    except Exception as e:
        app.logger.error(f"Error annotating text: {e}")
        return jsonify({'error': 'An error occurred while annotating text'}), 500

@app.route('/debug/db-status')
def debug_db_status():
    """Debug route to check database connection and table status"""
    from sqlalchemy import inspect, text
    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        db_uri = app.config['SQLALCHEMY_DATABASE_URI']
        # Mask credentials
        if '@' in db_uri:
            scheme_and_creds, rest = db_uri.split('@', 1)
            scheme = scheme_and_creds.split('://')[0]
            masked_uri = f"{scheme}://***@{rest}"
        else:
            masked_uri = db_uri

        table_counts = {}
        for table in tables:
            try:
                result = db.session.execute(text(f'SELECT COUNT(*) FROM "{table}"'))
                table_counts[table] = result.scalar()
            except Exception as e:
                table_counts[table] = f"error: {e}"

        return jsonify({
            'db_uri_masked': masked_uri,
            'is_sqlite': 'sqlite' in db_uri,
            'is_postgres': 'postgresql' in db_uri or 'postgres' in db_uri,
            'tables_found': tables,
            'table_row_counts': table_counts
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug/load-characters')
def debug_load_characters():
    """Manually trigger character loading from characters.txt into the database."""
    from sqlalchemy import text
    try:
        db.create_all()
        # Widen columns if they are still VARCHAR(100)/VARCHAR(50) from old schema
        try:
            db.session.execute(text('ALTER TABLE character ALTER COLUMN meaning TYPE TEXT'))
            db.session.execute(text('ALTER TABLE character ALTER COLUMN pinyin TYPE VARCHAR(200)'))
            db.session.commit()
        except Exception:
            db.session.rollback()  # already the right type, ignore
        char_count = Character.query.count()
        if char_count > 0:
            return jsonify({'message': f'Database already has {char_count} characters, no action needed.'})

        characters_file = os.path.join(os.path.dirname(__file__), 'characters.txt')
        if not os.path.exists(characters_file):
            return jsonify({'error': f'characters.txt not found at {characters_file}'}), 404

        with open(characters_file, 'r', encoding='utf-8') as f:
            count = 0
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) >= 5:
                    try:
                        rank = int(parts[0])
                        hanzi = parts[1]
                        frequency = int(float(parts[2]))
                        pinyin_with_meaning = parts[4:]
                        pinyin = pinyin_with_meaning[0].split()[0]
                        meaning = ' '.join(pinyin_with_meaning[0].split()[1:])
                        if len(pinyin_with_meaning) > 1:
                            meaning += ' ' + ' '.join(pinyin_with_meaning[1:])
                        meaning = meaning.strip()
                        character = Character(
                            hanzi=hanzi, pinyin=pinyin, meaning=meaning,
                            frequency=frequency, rank=rank
                        )
                        db.session.add(character)
                        count += 1
                    except Exception as e:
                        pass  # skip bad lines

        db.session.commit()
        final_count = Character.query.count()
        return jsonify({'loaded': count, 'verified_in_db': final_count})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/debug/oauth-uri')
def debug_oauth_uri():
    """Debug route to show the exact OAuth redirect URI"""
    redirect_uri = get_redirect_uri('google_auth')
    railway_url = os.environ.get('RAILWAY_STATIC_URL', 'Not set')
    port = os.environ.get('PORT', 'Not set')
    host = os.environ.get('HOST', 'Not set')
    
    debug_info = {
        'redirect_uri': redirect_uri,
        'request_host': request.host,
        'request_scheme': request.scheme,
        'full_request_url': request.url,
        'railway_static_url': railway_url,
        'port': port,
        'host': host,
        'headers': dict(request.headers),
        'server_name': request.environ.get('SERVER_NAME', 'Not set'),
        'http_host': request.environ.get('HTTP_HOST', 'Not set')
    }
    
    return jsonify(debug_info)

_db_initialized = False

@app.before_request
def ensure_db_initialized():
    """Create tables and load characters on the very first request (works reliably under gunicorn)."""
    global _db_initialized
    if _db_initialized:
        return
    _db_initialized = True

    print("=== DB initialization (first request) ===")
    from sqlalchemy import text

    # Check if we're in development or production
    is_dev_mode = not is_production and os.environ.get('FLASK_ENV') != 'production'

    if is_dev_mode and os.environ.get('RESET_DB') == 'true':
        print("Development mode with RESET_DB=true: Dropping all tables to update schema...")
        db.drop_all()
        db.create_all()
    else:
        print("Creating tables if they don't exist...")
        db.create_all()

    # Widen columns if they are still VARCHAR(100)/VARCHAR(50) from old schema
    try:
        db.session.execute(text('ALTER TABLE character ALTER COLUMN meaning TYPE TEXT'))
        db.session.execute(text('ALTER TABLE character ALTER COLUMN pinyin TYPE VARCHAR(200)'))
        db.session.commit()
        print("Widened meaning/pinyin columns")
    except Exception:
        db.session.rollback()

    # Initialize the database with characters from characters.txt
    try:
        char_count = Character.query.count()
        print(f"Characters currently in database: {char_count}")
        if char_count == 0:
            print("Initializing database with characters from characters.txt...")
            characters_file = os.path.join(os.path.dirname(__file__), 'characters.txt')
            print(f"Looking for characters file at: {characters_file}")

            if os.path.exists(characters_file):
                print("Characters file found, loading data...")
                with open(characters_file, 'r', encoding='utf-8') as f:
                    count = 0
                    for line in f:
                        parts = line.strip().split('\t')
                        if len(parts) >= 5:
                            try:
                                rank = int(parts[0])
                                hanzi = parts[1]
                                frequency = int(float(parts[2]))

                                pinyin_with_meaning = parts[4:]
                                pinyin = pinyin_with_meaning[0].split()[0]

                                meaning = ' '.join(pinyin_with_meaning[0].split()[1:])
                                if len(pinyin_with_meaning) > 1:
                                    meaning += ' ' + ' '.join(pinyin_with_meaning[1:])
                                meaning = meaning.strip()

                                if count < 5:
                                    print(f"Parsed: Rank={rank}, Hanzi='{hanzi}', Pinyin='{pinyin}', Meaning='{meaning}'")

                                character = Character(
                                    hanzi=hanzi,
                                    pinyin=pinyin,
                                    meaning=meaning,
                                    frequency=frequency,
                                    rank=rank
                                )
                                db.session.add(character)
                                count += 1
                            except Exception as e:
                                print(f"Error parsing line: {line.strip()}, Error: {e}")

                db.session.commit()
                final_count = Character.query.count()
                print(f"Committed {count} characters. Verified count in DB: {final_count}")
            else:
                print(f"WARNING: characters.txt file not found at {characters_file}")
        else:
            print(f"Database already has {char_count} characters, skipping import.")
    except Exception as e:
        print(f"ERROR initializing database with characters: {e}")
        import traceback
        traceback.print_exc()
        db.session.rollback()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8093))
    app.run(host='0.0.0.0', port=port, debug=True)
