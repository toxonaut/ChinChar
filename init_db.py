from app import app, db
from models import Character
import os

def init_db():
    """Initialize the database with Chinese characters from characters.txt file"""
    with app.app_context():
        # Create tables
        db.create_all()
        
        # Check if we already have characters
        if Character.query.count() > 0:
            print("Database already contains characters. Skipping initialization.")
            return
        
        # Get the path to characters.txt
        characters_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'characters.txt')
        
        # Check if the file exists
        if not os.path.exists(characters_file):
            print(f"Error: {characters_file} not found.")
            return
            
        # Read characters from file
        characters = []
        try:
            with open(characters_file, 'r', encoding='utf-8') as file:
                for line in file:
                    # Skip empty lines and metadata lines
                    line = line.strip()
                    if not line or line.startswith("Number of") or line.startswith("Total number"):
                        continue
                    
                    # Parse line - tab separated values
                    # Format: rank, hanzi, frequency, cumulative, pinyin, meaning
                    parts = line.split('\t')
                    
                    # Some lines might have extra spaces in the fields
                    parts = [p.strip() for p in parts]
                    
                    # Need at least rank, hanzi, frequency, and pinyin
                    if len(parts) >= 5:
                        try:
                            rank = int(parts[0])
                            hanzi = parts[1]
                            frequency = int(float(parts[2]))
                            pinyin = parts[4]
                            
                            # Meaning might be missing in some lines
                            meaning = parts[5] if len(parts) > 5 else ""
                            
                            characters.append((hanzi, pinyin, meaning, frequency, rank))
                        except (ValueError, IndexError) as e:
                            print(f"Warning: Error parsing line: {line}. Error: {e}")
                    else:
                        print(f"Warning: Line has incorrect format: {line}")
        except Exception as e:
            print(f"Error reading characters file: {e}")
            return
            
        if not characters:
            print("No characters found in the file.")
            return
            
        # Add characters to the database
        for hanzi, pinyin, meaning, frequency, rank in characters:
            character = Character(hanzi=hanzi, pinyin=pinyin, meaning=meaning, frequency=frequency, rank=rank)
            db.session.add(character)
        
        # Commit changes
        db.session.commit()
        print(f"Added {len(characters)} characters to the database.")

if __name__ == "__main__":
    init_db()
