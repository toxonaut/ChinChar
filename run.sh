#!/bin/bash

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from .env.example..."
    cp .env.example .env
    echo ".env file created. Please check the configuration."
fi

# Kill any process using port 8093
echo "Checking for processes using port 8093..."
pid=$(lsof -ti:8093)
if [ ! -z "$pid" ]; then
    echo "Killing process $pid using port 8093..."
    kill -9 $pid
fi

# Create .env file from example if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from example..."
    cp .env.example .env
fi

# Start the Flask app
echo "Starting Character Master on http://localhost:8093..."
python3 app.py
