"""Synthetic multi-culture name set for fairness tests (fictional people only).

Covers family-name-first orders, hyphenated and multi-part names, single
names, particles, and names transliterated from non-Latin scripts.
"""

NAMES: dict[str, list[str]] = {
    "Vietnamese (family first)": ["Nguyen Van An", "Tran Thi Mai", "Le Hoang Nam"],
    "Chinese (family first)": ["Wang Xiaoming", "Zhang Wei", "Chen Yuting"],
    "Korean (family first, hyphenated)": ["Park Ji-hoon", "Kim Min-seo"],
    "Japanese (family first)": ["Sato Haruka"],
    "Indian": ["Priya Raghunathan", "Arjun Venkataraman", "Sukhdeep Kaur"],
    "Nepali": ["Sabin Shrestha", "Anisha Gurung"],
    "Sri Lankan": ["Thushara Wickramasinghe"],
    "Bangladeshi": ["Mohammad Rahim Uddin"],
    "Filipino (multi-part)": ["Maria Clara Dela Cruz"],
    "Indonesian (single name)": ["Wulandari", "Sutrisno"],
    "Arabic (particles, hyphen)": ["Fatima Al-Zahrani", "Omar Abdullah Haddad"],
    "Persian": ["Reza Ahmadi"],
    "Nigerian": ["Chukwuemeka Okonkwo", "Ngozi Adeyemi"],
    "Kenyan": ["Wanjiru Kamau"],
    "Thai": ["Somchai Wongsakul"],
    "Mongolian": ["Batbayar Dorj"],
    "Spanish (hyphenated)": ["José García-López"],
    "Pacific": ["Sione Tupou", "Losana Ratu"],
    "Anglo": ["Emily Carter", "James O'Neill"],
}

# Sentences in which another person is named in free text (no known values).
TEMPLATES = [
    "I worked with {name} at the clinic last year.",
    "My mentor {name} encouraged me to apply.",
    "During the project, {name} and I organised a health workshop.",
]
