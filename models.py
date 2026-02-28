from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import random
from flask_login import UserMixin

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=True)
    profile_pic = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, default=datetime.utcnow)
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    encrypted_api_key = db.Column(db.Text, nullable=True)
    
    def __repr__(self):
        return f'<User {self.email}>'

class Character(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    hanzi = db.Column(db.String(10), nullable=False)
    pinyin = db.Column(db.String(200), nullable=False)
    meaning = db.Column(db.Text, nullable=False)
    frequency = db.Column(db.Integer, default=0)  # Store frequency from characters.txt
    rank = db.Column(db.Integer, default=0)  # Store rank based on frequency
    
    def __repr__(self):
        return f'<Character {self.hanzi}>'

class UserProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    familiarity = db.Column(db.Integer, default=0)  # 0: Don't know, 1: Unsure, 2: Know
    last_reviewed = db.Column(db.DateTime, default=datetime.utcnow)
    review_count = db.Column(db.Integer, default=0)
    know_count = db.Column(db.Integer, default=0)  # Number of times marked as "Know"
    unsure_count = db.Column(db.Integer, default=0)  # Number of times marked as "Unsure"
    dont_know_count = db.Column(db.Integer, default=0)  # Number of times marked as "Don't Know"
    
    character = db.relationship('Character', backref=db.backref('progress', lazy=True))
    user = db.relationship('User', backref=db.backref('progress', lazy=True))
    
    def __repr__(self):
        return f'<UserProgress user_id={self.user_id} character_id={self.character_id} familiarity={self.familiarity}>'

class UserCharacterTuning(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    rank_penalty = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'character_id', name='uq_user_character_tuning_user_character'),
    )

    character = db.relationship('Character', backref=db.backref('tuning', lazy=True))
    user = db.relationship('User', backref=db.backref('character_tuning', lazy=True))

    def __repr__(self):
        return f'<UserCharacterTuning user_id={self.user_id} character_id={self.character_id} rank_penalty={self.rank_penalty}>'

class CharacterAIDescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False, unique=True)
    content = db.Column(db.Text, nullable=False)
    model = db.Column(db.String(50), nullable=False, default='gpt-4.1')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    character = db.relationship('Character', backref=db.backref('ai_description', uselist=False))

    def __repr__(self):
        return f'<CharacterAIDescription character_id={self.character_id} model={self.model}>'

def get_rank_penalties(user_id):
    records = UserCharacterTuning.query.filter_by(user_id=user_id).all()
    return {r.character_id: r.rank_penalty for r in records}

def _weighted_pick(characters, rank_penalties):
    """Pick a character using weighted random selection.
    Characters with lower effective rank (= more common, less demoted) are
    much more likely to be chosen.  Weight = 1 / (effective_rank) so a
    demoted character genuinely appears less often.
    """
    weights = []
    for c in characters:
        effective_rank = c.rank + rank_penalties.get(c.id, 0)
        # Ensure effective_rank is at least 1 to avoid division by zero
        weights.append(1.0 / max(effective_rank, 1))
    return random.choices(characters, weights=weights, k=1)[0]

def get_next_character(user_id):
    """
    Get the next character to review based on frequency and familiarity.
    Uses a sliding window of the 100 most common characters that aren't yet known.
    Occasionally re-tests known characters (about 1 in 20 times).
    """
    try:
        # First, check if we have any characters in the database
        total_characters = Character.query.count()
        if total_characters == 0:
            print("No characters in database")
            return None
            
        # Get all characters that have been reviewed by this user and their familiarity
        progress_records = UserProgress.query.filter_by(user_id=user_id).all()

        # Create a dictionary of character_id -> familiarity for quick lookup
        familiarity_dict = {p.character_id: p.familiarity for p in progress_records}

        rank_penalties = get_rank_penalties(user_id)
        
        # Get all character IDs that have been marked as known (familiarity = 2)
        known_ids = [char_id for char_id, familiarity in familiarity_dict.items() if familiarity == 2]
        
        # Count how many characters the user has reviewed
        reviewed_count = len(familiarity_dict)
        print(f"User has reviewed {reviewed_count} characters")
        print(f"User knows {len(known_ids)} characters")
        
        # Keep track of the last shown character to avoid repetition
        last_shown_id = None
        last_progress = UserProgress.query.filter_by(user_id=user_id).order_by(UserProgress.last_reviewed.desc()).first()
        if last_progress:
            last_shown_id = last_progress.character_id
            print(f"Last shown character ID: {last_shown_id}")
        
        # For beginners (fewer than 20 characters reviewed), focus on the absolute most common characters
        if reviewed_count < 20:
            base_candidates = Character.query.order_by(Character.rank.asc()).limit(200).all()
            base_candidates.sort(key=lambda c: (c.rank + rank_penalties.get(c.id, 0), c.rank))
            top_characters = base_candidates[:20]
            top_ids = [char.id for char in top_characters]
            
            # First priority: Show unreviewed characters from the top 20
            unreviewed_top = [char for char in top_characters if char.id not in familiarity_dict and char.id != last_shown_id]
            if unreviewed_top:
                return _weighted_pick(unreviewed_top, rank_penalties)
            
            # Second priority: Show characters from top 20 that aren't well known
            not_well_known = [char for char in top_characters if char.id in familiarity_dict and familiarity_dict[char.id] < 2 and char.id != last_shown_id]
            if not_well_known:
                return _weighted_pick(not_well_known, rank_penalties)
            
            # If all top 20 are known, fall through to the main algorithm
        
        # Decide whether to show a known character (1 in 10 chance, or about 10%)
        show_known = random.random() < 0.1
        
        if show_known and known_ids:
            # Select a random known character, avoiding the last shown one if possible
            available_known = [char_id for char_id in known_ids if char_id != last_shown_id]
            if not available_known and known_ids:  # If only the last shown is known
                available_known = known_ids
                
            if available_known:
                characters = Character.query.filter(Character.id.in_(available_known)).all()
                if characters:
                    return _weighted_pick(characters, rank_penalties)
        
        # Main algorithm: Get the 100 most common characters that aren't yet known
        
        # First, get all characters that are marked as known
        known_characters_subquery = db.session.query(UserProgress.character_id).filter(
            UserProgress.user_id == user_id,
            UserProgress.familiarity == 2
        ).subquery()
        
        base_candidates = Character.query.filter(
            ~Character.id.in_(known_characters_subquery)
        ).order_by(Character.rank.asc()).limit(300).all()

        base_candidates.sort(key=lambda c: (c.rank + rank_penalties.get(c.id, 0), c.rank))
        next_characters = base_candidates[:100]
        
        if next_characters:
            # Filter out the last shown character if possible
            available_next = [char for char in next_characters if char.id != last_shown_id]
            if not available_next:
                available_next = next_characters
                
            if available_next:
                return _weighted_pick(available_next, rank_penalties)
        
        # If somehow all characters are known (extremely rare), show a random one from the top 100
        return Character.query.order_by(Character.rank.asc()).limit(100).first()
    
    except Exception as e:
        print(f"Error in get_next_character: {e}")
        # Fallback to a random character
        return Character.query.order_by(db.func.random()).first()

def update_progress(user_id, character_id, familiarity):
    """
    Update the user's progress for a character.
    
    Args:
        user_id: The ID of the user
        character_id: The ID of the character
        familiarity: 0 (Don't know), 1 (Unsure), or 2 (Know)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Find existing progress record
        progress = UserProgress.query.filter_by(user_id=user_id, character_id=character_id).first()
        
        if not progress:
            # Create a new progress record
            progress = UserProgress(
                user_id=user_id,
                character_id=character_id, 
                familiarity=familiarity, 
                last_reviewed=datetime.utcnow(),
                review_count=1
            )
            
            # Set the appropriate count based on familiarity
            if familiarity == 0:
                progress.dont_know_count = 1
            elif familiarity == 1:
                progress.unsure_count = 1
            elif familiarity == 2:
                progress.know_count = 1
                
            db.session.add(progress)
        else:
            # Update existing record
            progress.familiarity = familiarity
            progress.last_reviewed = datetime.utcnow()
            progress.review_count += 1
            
            # Increment the appropriate count based on familiarity
            if familiarity == 0:
                progress.dont_know_count += 1
            elif familiarity == 1:
                progress.unsure_count += 1
            elif familiarity == 2:
                progress.know_count += 1
        
        db.session.commit()
        return True
    except Exception as e:
        print(f"Error updating progress: {e}")
        db.session.rollback()
        return False
