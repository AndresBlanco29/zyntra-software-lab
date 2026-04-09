US_STATE_CITIES = {
    'Alabama': ['Birmingham', 'Montgomery', 'Mobile', 'Huntsville', 'Tuscaloosa', 'Hoover', 'Dothan'],
    'Alaska': ['Anchorage', 'Fairbanks', 'Juneau', 'Wasilla', 'Sitka', 'Ketchikan'],
    'Arizona': ['Phoenix', 'Tucson', 'Mesa', 'Scottsdale', 'Glendale', 'Tempe', 'Chandler'],
    'Arkansas': ['Little Rock', 'Fort Smith', 'Fayetteville', 'Springdale', 'Jonesboro', 'Conway'],
    'California': ['Los Angeles', 'San Diego', 'San Jose', 'San Francisco', 'Sacramento', 'Fresno', 'Long Beach', 'Oakland'],
    'Colorado': ['Denver', 'Colorado Springs', 'Aurora', 'Fort Collins', 'Boulder', 'Lakewood'],
    'Connecticut': ['Bridgeport', 'New Haven', 'Stamford', 'Hartford', 'Waterbury', 'Norwalk'],
    'Delaware': ['Wilmington', 'Dover', 'Newark', 'Middletown', 'Smyrna'],
    'Florida': ['Miami', 'Orlando', 'Tampa', 'Jacksonville', 'Tallahassee', 'Fort Lauderdale', 'Hialeah', 'St. Petersburg'],
    'Georgia': ['Atlanta', 'Augusta', 'Savannah', 'Columbus', 'Macon', 'Athens', 'Roswell', 'Sandy Springs'],
    'Hawaii': ['Honolulu', 'Hilo', 'Kailua', 'Pearl City', 'Kahului'],
    'Idaho': ['Boise', 'Meridian', 'Nampa', 'Idaho Falls', 'Pocatello', 'Caldwell'],
    'Illinois': ['Chicago', 'Aurora', 'Naperville', 'Springfield', 'Peoria', 'Rockford', 'Joliet'],
    'Indiana': ['Indianapolis', 'Fort Wayne', 'Evansville', 'South Bend', 'Carmel', 'Bloomington'],
    'Iowa': ['Des Moines', 'Cedar Rapids', 'Davenport', 'Sioux City', 'Iowa City', 'West Des Moines'],
    'Kansas': ['Wichita', 'Overland Park', 'Kansas City', 'Topeka', 'Lawrence', 'Olathe'],
    'Kentucky': ['Louisville', 'Lexington', 'Bowling Green', 'Owensboro', 'Covington', 'Richmond'],
    'Louisiana': ['New Orleans', 'Baton Rouge', 'Shreveport', 'Lafayette', 'Lake Charles', 'Metairie'],
    'Maine': ['Portland', 'Lewiston', 'Bangor', 'South Portland', 'Auburn'],
    'Maryland': ['Baltimore', 'Annapolis', 'Frederick', 'Rockville', 'Gaithersburg', 'Bowie'],
    'Massachusetts': ['Boston', 'Worcester', 'Springfield', 'Cambridge', 'Lowell', 'Quincy'],
    'Michigan': ['Detroit', 'Grand Rapids', 'Warren', 'Lansing', 'Ann Arbor', 'Flint'],
    'Minnesota': ['Minneapolis', 'Saint Paul', 'Rochester', 'Duluth', 'Bloomington', 'Brooklyn Park'],
    'Mississippi': ['Jackson', 'Gulfport', 'Southaven', 'Hattiesburg', 'Biloxi'],
    'Missouri': ['Kansas City', 'Saint Louis', 'Springfield', 'Columbia', 'Independence', 'Lee\'s Summit'],
    'Montana': ['Billings', 'Missoula', 'Great Falls', 'Bozeman', 'Helena'],
    'Nebraska': ['Omaha', 'Lincoln', 'Bellevue', 'Grand Island', 'Kearney'],
    'Nevada': ['Las Vegas', 'Henderson', 'Reno', 'North Las Vegas', 'Carson City', 'Sparks'],
    'New Hampshire': ['Manchester', 'Nashua', 'Concord', 'Derry', 'Dover'],
    'New Jersey': ['Newark', 'Jersey City', 'Paterson', 'Elizabeth', 'Trenton', 'Edison'],
    'New Mexico': ['Albuquerque', 'Santa Fe', 'Las Cruces', 'Rio Rancho', 'Roswell'],
    'New York': ['New York City', 'Buffalo', 'Rochester', 'Albany', 'Syracuse', 'Yonkers', 'White Plains'],
    'North Carolina': ['Charlotte', 'Raleigh', 'Greensboro', 'Durham', 'Asheville', 'Winston-Salem'],
    'North Dakota': ['Fargo', 'Bismarck', 'Grand Forks', 'Minot', 'West Fargo'],
    'Ohio': ['Columbus', 'Cleveland', 'Cincinnati', 'Toledo', 'Akron', 'Dayton'],
    'Oklahoma': ['Oklahoma City', 'Tulsa', 'Norman', 'Broken Arrow', 'Edmond'],
    'Oregon': ['Portland', 'Salem', 'Eugene', 'Gresham', 'Bend', 'Medford'],
    'Pennsylvania': ['Philadelphia', 'Pittsburgh', 'Allentown', 'Harrisburg', 'Erie', 'Reading'],
    'Rhode Island': ['Providence', 'Warwick', 'Cranston', 'Pawtucket', 'Newport'],
    'South Carolina': ['Columbia', 'Charleston', 'North Charleston', 'Greenville', 'Myrtle Beach', 'Spartanburg'],
    'South Dakota': ['Sioux Falls', 'Rapid City', 'Aberdeen', 'Brookings', 'Pierre'],
    'Tennessee': ['Nashville', 'Memphis', 'Knoxville', 'Chattanooga', 'Clarksville', 'Murfreesboro'],
    'Texas': ['Houston', 'Dallas', 'Austin', 'San Antonio', 'Fort Worth', 'El Paso', 'Arlington', 'Plano'],
    'Utah': ['Salt Lake City', 'West Valley City', 'Provo', 'West Jordan', 'Ogden', 'St. George'],
    'Vermont': ['Burlington', 'South Burlington', 'Rutland', 'Montpelier', 'Brattleboro'],
    'Virginia': ['Virginia Beach', 'Richmond', 'Norfolk', 'Arlington', 'Alexandria', 'Roanoke'],
    'Washington': ['Seattle', 'Spokane', 'Tacoma', 'Vancouver', 'Olympia', 'Bellevue'],
    'West Virginia': ['Charleston', 'Huntington', 'Morgantown', 'Parkersburg', 'Wheeling'],
    'Wisconsin': ['Milwaukee', 'Madison', 'Green Bay', 'Kenosha', 'Racine', 'Appleton'],
    'Wyoming': ['Cheyenne', 'Casper', 'Laramie', 'Gillette', 'Jackson']
}


def match_state_name(value):
    normalized = (value or '').strip().lower()
    for state_name in US_STATE_CITIES:
        if state_name.lower() == normalized:
            return state_name
    return None


def get_cities_for_state(state_name):
    matched_state = match_state_name(state_name)
    if not matched_state:
        return []
    return US_STATE_CITIES[matched_state]


def match_city_for_state(state_name, city_name):
    normalized_city = (city_name or '').strip().lower()
    for city in get_cities_for_state(state_name):
        if city.lower() == normalized_city:
            return city
    return None


def is_valid_city_for_state(state_name, city_name):
    return match_city_for_state(state_name, city_name) is not None