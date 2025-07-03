import uuid
import random
import httpx
from typing import List, Dict, Optional, Any
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---- OpenAPI metadata ----
app = FastAPI(
    title="ClueQuest: The Detective's Dilemma Backend API",
    description="Backend game logic for a detective mystery game. Endpoints for starting games, searching areas, and submitting guesses. See /docs for full API.",
    version="1.0.0",
    openapi_tags=[
        {"name": "game", "description": "Game lifecycle and interaction endpoints"},
    ]
)

# ---- CORS configuration ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Set your allowed origins here for security
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------ Models --------

class Suspect(BaseModel):
    id: str = Field(..., description="Suspect unique identifier")
    name: str = Field(..., description="Full name")
    gender: str = Field(..., description="Gender")
    age: int = Field(..., description="Age in years")
    seat: str = Field(..., description="Seat location name or number")
    picture: str = Field(..., description="URL for the suspect's portrait")
    habits: List[str] = Field(..., description="List of habits or quirks")


class Clue(BaseModel):
    id: str = Field(..., description="Clue unique identifier")
    text: str = Field(..., description="The clue's actual text, will be revealed after searching")
    relates_to: Optional[str] = Field(None, description="ID of related suspect or killer-specific")
    critical: bool = Field(False, description="Whether this clue is essential to identifying the killer")


class GameState(BaseModel):
    id: str = Field(..., description="Game ID")
    suspects: List[Suspect] = Field(..., description="List of all suspects")
    killer_id: str = Field(..., description="Suspect id who is the killer (hidden in API outputs unless game over)")
    clues: List[Clue] = Field(..., description="All clues for the game")
    clue_locations: Dict[str, Optional[str]] = Field(..., description="Location name -> clue_id or None if already found")
    searches_remaining: int = Field(..., description="Number of searches left in this game")
    found_clues: Dict[str, Clue] = Field(default_factory=dict, description="Discovered clues by location")
    state: str = Field(..., description="Game stage state: ongoing, win, lose")


class StartGameResponse(BaseModel):
    game_id: str = Field(..., description="ID of the new game")
    suspects: List[Suspect] = Field(..., description="Suspect list")
    seats: List[str] = Field(..., description="Seat labels")
    searches_remaining: int = Field(..., description="Number of allowed searches")
    found_clues: Dict[str, Any] = Field(..., description="Clues already revealed (initially empty)")
    clue_locations: List[str] = Field(..., description="Names of interactive search locations")
    # no killer, no actual clues content revealed yet


class SearchAreaRequest(BaseModel):
    game_id: str = Field(..., description="ID of the game")
    location: str = Field(..., description="Which location to search")


class SearchAreaResponse(BaseModel):
    found_clue: Optional[Clue] = Field(None, description="Clue revealed at this location (if any, otherwise null)")
    searches_remaining: int = Field(..., description="Searches left")
    found_clues: Dict[str, Clue] = Field(..., description="All clues found so far")
    state: str = Field(..., description="Game state after this search (ongoing/win/lose)")
    clue_locations: List[str] = Field(..., description="List of still available locations to search")


class SubmitGuessRequest(BaseModel):
    game_id: str = Field(..., description="Game id")
    suspect_id: str = Field(..., description="Chosen suspect id for final guess")


class SubmitGuessResponse(BaseModel):
    result: str = Field(..., description="Result of guess: win or lose")
    killer_id: str = Field(..., description="Actual killer id")
    timeline: List[str] = Field(..., description="Timeline events for replay on loss")
    message: str = Field(..., description="Narrative/game feedback for win/lose")


# ---------- Game Management (in-memory, production = swap for persistent store) ----------

GAME_STORE: Dict[str, GameState] = {}
GAME_SEARCH_LIMIT = 5
GAME_LOCATIONS = [
    "luggage",
    "CCTV",
    "restrooms",
    "seat_logs",
    "audio"
]
SEAT_LABELS = ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "C3", "X1"]

HABITS_LIST = [
    "reads mystery novels",
    "bites nails",
    "chews gum",
    "always carries coffee",
    "nervous tapper",
    "wears sunglasses indoors",
    "meticulously organized",
    "talks to self",
    "obsessed with clocks",
    "doodles constantly"
]

CLUES_POOL = [
    # pool of clues for distribution (mix of killer-specific and red herrings)
    {"text": "There was a coffee stain near the scene.", "critical": True},
    {"text": "Someone remembered seeing sunglasses left behind.", "critical": False},
    {"text": "A napkin with doodles was found.", "critical": False},
    {"text": "Unusual footsteps heard on CCTV at midnight.", "critical": True},
    {"text": "A book torn page found in the luggage.", "critical": False},
    {"text": "Nail clippings in the restroom bin.", "critical": False},
    {"text": "A nervous tapping noise in the audio.", "critical": True},
    {"text": "A perfectly organized wallet in the seat.", "critical": False},
    {"text": "Self talk recorded on the audio logs.", "critical": True},
    {"text": "An untouched coffee by the killer's seat.", "critical": True},
]

# ------------ Game Utility Logic ------------

async def fetch_random_users(n: int) -> List[dict]:
    """Fetches n random user profiles from randomuser.me API.
    Returns a list of dicts with required fields extracted."""
    url = f"https://randomuser.me/api/?results={n}&inc=name,gender,picture,dob"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise HTTPException(500, "Failed to fetch suspects profiles")
    data = resp.json()["results"]
    suspects = []
    for idx, su in enumerate(data):
        suspects.append({
            "name": f"{su['name']['first']} {su['name']['last']}",
            "gender": su['gender'].capitalize(),
            "age": su['dob']['age'],
            "picture": su['picture']['large'],
        })
    return suspects


def assign_habits(num: int) -> List[List[str]]:
    """Assigns random habits to each suspect."""
    result = []
    habits = HABITS_LIST[:]
    random.shuffle(habits)
    for i in range(num):
        # Each suspect gets 2 random habits (may overlap/have repeats if more suspects than unique habits)
        chosen = random.sample(HABITS_LIST, k=2)
        result.append(chosen)
    return result


def pick_killer(suspects: List[dict]) -> int:
    """Randomly pick one suspect as the killer by index."""
    return random.randint(0, len(suspects) - 1)


def assign_seats(num: int) -> List[str]:
    """Assigns a seat from labels to each suspect."""
    return SEAT_LABELS[:num]


def assign_clues(suspects: List[dict], killer_idx: int) -> (List[Clue], Dict[str, str]):
    """
    Assigns clues to game locations. Mixes killer-related and red herrings.
    Returns both:
      - clue list (with id),
      - mapping of location to clue id
    """
    clues = []
    pool = CLUES_POOL[:]
    random.shuffle(pool)
    locations = GAME_LOCATIONS[:]
    random.shuffle(locations)
    used_locs = locations[:len(pool)]
    clue_loc_map = {}
    for i, cp in enumerate(pool):
        clue_id = str(uuid.uuid4())
        # Map "critical True" clues to the killer (relates_to), others can be red herrings
        rel = None
        if cp["critical"]:
            rel = suspects[killer_idx]["name"]
        clue = Clue(id=clue_id, text=cp["text"], relates_to=rel, critical=cp["critical"])
        clues.append(clue)
        if i < len(used_locs):
            clue_loc_map[used_locs[i]] = clue_id
    for loc in set(GAME_LOCATIONS) - set(used_locs):
        clue_loc_map[loc] = None
    return clues, clue_loc_map


def hide_killer_info(gs: GameState) -> dict:
    """Returns the serialized GameState with killer/clues hidden for frontend consumption."""
    resp = {
        "game_id": gs.id,
        "suspects": [s.model_dump() for s in gs.suspects],
        "seats": [suspect.seat for suspect in gs.suspects],
        "searches_remaining": gs.searches_remaining,
        "found_clues": gs.found_clues,
        "clue_locations": list(gs.clue_locations.keys()),
    }
    return resp


# ----------- PUBLIC INTERFACE: Endpoints ------------

# PUBLIC_INTERFACE
@app.get("/start-game", response_model=StartGameResponse, tags=["game"], summary="Start a new game", description="Creates a new detective case, generates suspects, selects killer, and returns initial game state.")
async def start_game():
    """
    Starts a new detective game. Generates suspects (random profiles),
    selects the killer, assigns habits, clues, and prepares searchable areas.

    Returns (no solution spoilers!):
    - list of suspects (id, name, gender, age, seat, picture, and habits)
    - initial clue search areas (all unrevealed)
    - searches remaining
    """
    NUM_SUSPECTS = 10

    suspects_raw = await fetch_random_users(NUM_SUSPECTS)
    seats = assign_seats(NUM_SUSPECTS)
    habits_groups = assign_habits(NUM_SUSPECTS)
    suspects = []
    for i, sus in enumerate(suspects_raw):
        suspect_id = str(uuid.uuid4())
        suspects.append(
            Suspect(
                id=suspect_id,
                name=sus["name"],
                gender=sus["gender"],
                age=sus["age"],
                seat=seats[i],
                picture=sus["picture"],
                habits=habits_groups[i],
            )
        )

    killer_idx = pick_killer(suspects_raw)
    clues, clue_loc_map = assign_clues(suspects_raw, killer_idx)
    gs = GameState(
        id=str(uuid.uuid4()),
        suspects=suspects,
        killer_id=suspects[killer_idx].id,
        clues=clues,
        clue_locations=clue_loc_map,
        searches_remaining=GAME_SEARCH_LIMIT,
        found_clues={},
        state="ongoing"
    )
    GAME_STORE[gs.id] = gs

    return StartGameResponse(
        game_id=gs.id,
        suspects=gs.suspects,
        seats=[s.seat for s in gs.suspects],
        searches_remaining=gs.searches_remaining,
        found_clues={},
        clue_locations=list(gs.clue_locations.keys()),
    )


# PUBLIC_INTERFACE
@app.post("/search-area", response_model=SearchAreaResponse, tags=["game"], summary="Search an area for clues", description="Searches a specific area for a clue, decrements search limit, and returns any found clue and game state.")
def search_area(payload: SearchAreaRequest = Body(...)):
    """
    Search a specific area for clues. Only a limited number of searches allowed.
    If a clue is present, reveals it for the first time.
    Returns found clue (if any), updated found_clues, search count, and state.
    """
    gs = GAME_STORE.get(payload.game_id)
    if not gs or gs.state != "ongoing":
        raise HTTPException(404, "Game not found or not in progress")

    location = payload.location
    if location not in gs.clue_locations:
        raise HTTPException(400, "Invalid search location")

    if gs.searches_remaining <= 0:
        gs.state = "lose"
        return SearchAreaResponse(
            found_clue=None,
            searches_remaining=0,
            found_clues=gs.found_clues,
            state=gs.state,
            clue_locations=[loc for loc in gs.clue_locations if loc not in gs.found_clues]
        )

    found_clue = None
    clue_id = gs.clue_locations[location]
    if clue_id and location not in gs.found_clues:
        for clue in gs.clues:
            if clue.id == clue_id:
                found_clue = clue
                gs.found_clues[location] = clue
                break
        gs.clue_locations[location] = None  # Remove so can't find again

    gs.searches_remaining -= 1

    # If player finds 2 or more killer-critical clues, they get bonus info
    # (can adjust for win logic or just keep clue search mechanic strict)

    if gs.searches_remaining <= 0 and gs.state == "ongoing":
        gs.state = "lose"

    return SearchAreaResponse(
        found_clue=found_clue,
        searches_remaining=gs.searches_remaining,
        found_clues=gs.found_clues,
        state=gs.state,
        clue_locations=[loc for loc in gs.clue_locations if loc not in gs.found_clues]
    )


# PUBLIC_INTERFACE
@app.post("/submit-guess", response_model=SubmitGuessResponse, tags=["game"], summary="Submit a guess for the killer", description="Submits your guess for the killer. Ends the game with win/lose and provides a timeline summary.")
def submit_guess(payload: SubmitGuessRequest = Body(...)):
    """
    Make a final guess. Returns win/lose and replay timeline if lost.
    - If guess correct: win, narrative message.
    - If not: lose, reveal killer, show timeline of clues
    """
    gs = GAME_STORE.get(payload.game_id)
    if not gs or gs.state != "ongoing":
        raise HTTPException(404, "Game not found or already completed")
    gs.state = "lose"  # Will revert if win below

    if payload.suspect_id == gs.killer_id:
        gs.state = "win"
        # Construct positive feedback
        msg = ("Congratulations! You correctly deduced the killer "
               f"({get_suspect_name(gs, gs.killer_id)}). The case is closed.")
        timeline = [f"{get_suspect_name(gs, gs.killer_id)} was acting suspiciously throughout the journey."]
        return SubmitGuessResponse(
            result="win",
            killer_id=gs.killer_id,
            timeline=timeline,
            message=msg
        )
    else:
        # Reveal killer, show sequence of all clues and deduction pointers for replay
        timeline = []
        for location, clue in gs.found_clues.items():
            if clue.critical:
                timeline.append(f"Critical clue at {location}: {clue.text}")
            else:
                timeline.append(f"Red herring at {location}: {clue.text}")
        msg = (f"Wrong guess! The real killer was {get_suspect_name(gs, gs.killer_id)}. "
               "Review the clues and try again next time.")
        gs.state = "lose"
        return SubmitGuessResponse(
            result="lose",
            killer_id=gs.killer_id,
            timeline=timeline,
            message=msg
        )


def get_suspect_name(gs: GameState, sid: str):
    for s in gs.suspects:
        if s.id == sid:
            return s.name
    return "Unknown"


# PUBLIC_INTERFACE
@app.get("/", summary="Health check", description="Basic health check for service readiness.")
def health_check():
    """Basic health check endpoint."""
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.get("/help", tags=["game"], summary="API and WebSocket usage help", description="Describes endpoints and real-time usage tips.")
def api_help():
    return {
        "info": "Game API endpoints: /start-game [GET], /search-area [POST], /submit-guess [POST]. Start a game, search clue locations, and submit a final suspect. Uses HTTP only; no WebSocket in this backend."
    }
