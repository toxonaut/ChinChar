import os
from flask import Flask, render_template, request, jsonify, make_response, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from models import db, Character, UserProgress, get_next_character, update_progress, User, UserCharacterTuning
import random
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import hashlib
import string
from authlib.integrations.flask_client import OAuth

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
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///chinchar.db')
# Replace postgres:// with postgresql:// in the DATABASE_URL (Railway specific)
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Set a secret key for session management
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

# Configure session to be more robust
app.config['SESSION_COOKIE_SECURE'] = is_production  # Set to True in production with HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

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
        
        # Log in the user
        login_user(user)
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
            
            # Log in the user
            login_user(user)
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
        
        # Create a dictionary to store character progress
        progress_data = {
            "know": [],
            "unsure": [],
            "dont_know": [],
            "detailed": {}  # New section for detailed progress info
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
                    "last_reviewed": p.last_reviewed.isoformat()
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

# Create tables within application context
with app.app_context():
    # Check if we're in development or production
    is_dev_mode = not is_production and os.environ.get('FLASK_ENV') != 'production'
    
    if is_dev_mode and os.environ.get('RESET_DB') == 'true':
        # Only drop tables in development mode and when explicitly requested
        print("Development mode with RESET_DB=true: Dropping all tables to update schema...")
        db.drop_all()
        db.create_all()
    else:
        # In production or normal development, just create tables if they don't exist
        print("Creating tables if they don't exist...")
        db.create_all()
    
    # Initialize the database with characters from characters.txt
    try:
        # Check if there are any characters in the database
        if Character.query.count() == 0:
            print("Initializing database with characters from characters.txt...")
            characters_file = os.path.join(os.path.dirname(__file__), 'characters.txt')
            print(f"Looking for characters file at: {characters_file}")
            
            # List files in the current directory for debugging
            print(f"Files in {os.path.dirname(__file__)}:")
            for file in os.listdir(os.path.dirname(__file__)):
                print(f"  - {file}")
            
            if os.path.exists(characters_file):
                print(f"Characters file found, loading data...")
                with open(characters_file, 'r', encoding='utf-8') as f:
                    count = 0
                    for line in f:
                        parts = line.strip().split('\t')
                        if len(parts) >= 5:  # We need at least rank, hanzi, frequency, cumulative, pinyin
                            try:
                                rank = int(parts[0])
                                hanzi = parts[1]
                                frequency = int(float(parts[2]))
                                
                                # Extract pinyin and meaning
                                pinyin_with_meaning = parts[4:]
                                pinyin = pinyin_with_meaning[0].split()[0]  # Get the first word of pinyin
                                
                                # Join the rest as meaning
                                meaning = ' '.join(pinyin_with_meaning[0].split()[1:])
                                if len(pinyin_with_meaning) > 1:
                                    meaning += ' ' + ' '.join(pinyin_with_meaning[1:])
                                
                                # Clean up meaning
                                meaning = meaning.strip()
                                
                                # Print debug info for the first few characters
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
                print(f"Initialized database with {count} characters")
            else:
                print(f"Warning: characters.txt file not found at {characters_file}")
    except Exception as e:
        print(f"Error initializing database: {e}")
        db.session.rollback()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8093))
    app.run(host='0.0.0.0', port=port, debug=True)
