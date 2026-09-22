"""Facts + system prompt for Zero40 Brewing (Financial District / Kokapet).

Client-demo config sourced from public listings, not a live client
handoff — hours, prices, and address need a client confirmation pass
before this goes near a real phone line. Structure mirrors
spice_route_kitchen.py; app/pipeline/prompts.py appends further instructions.
"""

from __future__ import annotations

from app.config.restaurants import Restaurant, TopicFacts

_MENU_TOPIC = TopicFacts(
    keywords=(
        "menu",
        "food",
        "eat",
        "dish",
        "drink",
        "beer",
        "beers",
        "brew",
        "pizza",
        "burger",
        "starter",
        "salad",
        "tandoor",
        "bbq",
        "biryani",
        "dessert",
        "breakfast",
        "brunch",
        "coffee",
        "cocktail",
        "mocktail",
        "spirit",
        "whiskey",
        "whisky",
        "vodka",
        "wine",
        "vegetarian",
        "vegan",
        "veg",
        "allergen",
        "allergy",
        "gluten",
        "price",
        "cost",
    ),
    text="""Top/signature dishes: Beer Batter Fish, Cheesy Broccoli, Butter Chicken Pizza, Hefeweizen, and Craft/Brewed Beer generally.

Menu highlights (full menu is much larger — these are the categories and standouts to mention):
- Our Beers (house-brewed, 7-8 varieties, rotating seasonals): Old Timer (Witbier), Blue Camel (Hefeweizen), Beach Bum (Pale Ale), Go Swami (Helles), Shavasan (Stout), Vincent Van Goat (Weizenbock)
- Starters/"Brew's BFF" & "Gobble Up": Gobi 65, Chicken 65, Lamb Galouti Bites, Masala Calamari, Beef Bulgogi
- Salads: Spinach Mango Bhel Salad, Caesar Salad, Zero40 Salad, Greek Feta Salad
- Burgers/"Buns of Glory": Chicken Steak Burger, Hot Fried Chicken Burger, Farmer Joe Burger, Miss Daisy
- Pizza (also Pizza Dosa): Margherita, Butter Chicken Pizza, Pepperoni, All Meat Pizza
- Tandoor & BBQ: Paneer Tikka, Murgh Tikka, Patthar Ka Gosht, Tandoori Lamb Chops
- Mains/"Big Chews": Butter Chicken & Naan, Zero40 Murgh Tikka Biryani, Fish & Chips, Thai Curry
- Breads & Rice: Baby Naan, Tandoori Roti, Dosa, Steamed Rice
- Desserts: Basque Burnt Cheesecake, Hyderabadi Cheesecake, Tiramisu, Brownie Sundae
- Hangover Breakfast (weekend brunch): Eggs Benedict, Big Breakfast, Brioche French Toast
- Coffees & drinks: Espresso Coffees, Mood Coffee & Drinks, Iced Coffees, Hot Teas, Milkshakes
- Cocktails & shots: Classic Cocktails, Beer Cocktails, Zero40 signature cocktails, Shots (e.g. Martini, Mojito, Margarita)
- Mocktails & soft beverages: several non-alcoholic options
- Spirits: full bar — single malts, whiskey, vodka, gin, rum, tequila, liqueurs, cognac, champagne/sparkling wine, wines, brandy
Prices roughly range ₹75 (breads/rice) to ₹700+ (specialty pizzas/cocktails); a full detailed menu with exact prices is available on request — don't quote exact prices unless asked, and if pressed for an exact price you're not sure of, offer to have the owner confirm rather than guessing.
Dietary: Most starters, salads, pizzas, and mains have a vegetarian option — the kitchen can confirm specifics; don't guess on allergens.""",
)

_MEMBERSHIP_TOPIC = TopicFacts(
    keywords=("membership", "member", "tribe", "loyalty", "perk", "regular", "stein", "merch"),
    text="""Membership: The Tribe — an annual membership (₹7,999) that includes one free pint of any craft beer per day, a branded beer stein, and exclusive merchandise. Mention it if a caller asks about regulars' perks or loyalty programs.""",
)

_SEATING_TOPIC = TopicFacts(
    keywords=(
        "seating",
        "ambience",
        "ambiance",
        "atmosphere",
        "music",
        "live",
        "pool table",
        "sports",
        "screen",
        "terrace",
        "garden",
        "lounge",
        "booth",
        "outdoor",
        "indoor",
        "romantic",
        "party",
        "vibe",
    ),
    text="""Seating & ambience: Four levels of seating, including a beer garden/outdoor terrace, indoor seating, lounge and booth seating, and bar seating. Live music and live entertainment on select nights, a pool table, and live sports screening. Good for a party vibe as well as romantic/quieter dining depending on where you're seated.""",
)

_ACCESSIBILITY_TOPIC = TopicFacts(
    keywords=("wheelchair", "accessible", "accessibility", "disability", "disabled", "ramp"),
    text="""Accessibility: Wheelchair accessible, step-free entry.""",
)

_POLICY_COST_TOPIC = TopicFacts(
    keywords=(
        "stag",
        "smoking",
        "smoke",
        "rating",
        "review",
        "reputation",
        "zomato",
        "expensive",
        "budget",
        "for two",
    ),
    text="""Policy: Full bar available; stags (single male guests) are allowed; a designated smoking area is available; venue is 21+ for alcohol service.
Cost: Roughly ₹3,500 for two with alcohol; about ₹350 for a pint of beer. Exclusive of applicable taxes.
Rating: 4.4 out of 5 on Zomato (7,000+ ratings) — mention only if a caller asks about reviews/reputation.""",
)

ZERO40_BREWING = Restaurant(
    name="Zero40 Brewing",
    bot_name="Anil",
    first_message=(
        "Thanks for calling Zero40 Brewing, this is Anil — how can I help?"
    ),
    end_call_message="Thanks so much for calling Zero40 Brewing — cheers, and talk soon!",
    timezone="Asia/Kolkata",
    hours={
        0: [("12:00", "23:55")],  # Monday
        1: [("12:00", "23:55")],  # Tuesday
        2: [("12:00", "23:55")],  # Wednesday
        3: [("12:00", "23:55")],  # Thursday
        4: [("12:00", "23:55")],  # Friday
        5: [("12:00", "23:55")],  # Saturday
        6: [("10:00", "23:55")],  # Sunday — brunch/breakfast starts earlier
    },
    system_prompt="""You are Anil, a warm and efficient phone receptionist for Zero40 Brewing, a microbrewery and pub in India. You answer every call directly — be a normal, friendly brewery receptionist.

=== ZERO40 FACTS (sourced from zero40.com and public listings, not confirmed directly with the client — verify hours/prices/address before this goes live) ===
Name: Zero40 Brewing
Tagline: Hyderabad's favourite brewery — "dialed in since 2016"
Type: Microbrewery & pub — craft beer brewed in-house, plus a full food menu
Cuisines: American, Pizza, North Indian, Fast Food, Continental, Beverages, Alcoholic Beverages, Bar Food
Address: Financial District, Gowlidoddy, Nanakramguda, Hyderabad, Telangana 500032 (Kokapet area)
Phone: 72079 11036 / 72079 11039 / 72079 11040
Hours: 12:00 PM – 11:55 PM daily, Sunday opens earlier at 10:00 AM for the breakfast menu. (A second Zero40 location exists in Jubilee Hills — if a caller asks about that branch specifically, note it's a different location and take their name/number for the owner to redirect them.)
Reservations: We take table reservations for any group size — get their name and the date/time they'd like. Larger groups (8+) or private events should also be told about party packages and Room Two (a private event space) — mention it's available and offer to have the owner follow up with details.
Parking: Both free/on-site parking and valet parking available; accessible parking too.
Payment: Cash, cards, UPI, and other digital payments.
=== END FACTS ===

Your job on every call:
1. Greet the caller and find out what they need.
2. If they want to make a reservation, get their name, guest count, and date/time (no phone number needed). For groups of 8 or more, also mention party packages/Room Two are available and offer to have the owner follow up with details.
3. If their question is covered by the facts above (hours, location, menu highlights, cuisines, membership, parking, payment, seating/ambience, accessibility, cost for two, age/alcohol policy), answer it directly and confidently, in a short conversational sentence — do not say "according to my information" or mention that you're reading from notes.
4. If you don't know the answer (specific allergens/ingredients, exact prices, large private events beyond what's noted above, complaints, anything not in the facts above, or anything you're not fully sure of), do NOT guess. Say something like "Let me take your name and number so the owner can call you back on that" — then collect their name, callback phone number, and their exact question.
5. If a caller pushes back, repeats, or rephrases a request after you've already declined it per policy (e.g. asking again for something outside the facts, or for an exception you can't grant), treat that as real interest the owner should know about — say something like "I can't do that myself, but let me pass this along so the owner can decide" and take their name and callback number, even though your answer to them stays the same.

Rules:
- Never invent menu items, prices, ingredients, or allergen information you don't have. Food and alcohol allergies are serious — always say the owner or kitchen will confirm directly rather than guessing.
- Never confirm alcohol service to a caller who says they're a minor, and if asked about age policy, say the venue is 21+ for alcohol service and let the owner handle specifics if pushed further.
- If it sounds like a genuine emergency (fire, medical, safety), tell them to call 112 right away — India's nationwide emergency number for police, fire, and medical — then end the call so they can dial.
- Never mention tool names, JSON, or "the system" to the caller.
""",
    topic_facts=(_MENU_TOPIC, _MEMBERSHIP_TOPIC, _SEATING_TOPIC, _ACCESSIBILITY_TOPIC, _POLICY_COST_TOPIC),
    # Not set: front-desk numbers above aren't a real person's cell.
    # Get the actual owner/manager's number from the client first.
    owner_phone="",
)
