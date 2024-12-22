# translate
A python program to use the OpenAI API to translate web novels

This is program to help you translate web novels (presently I use it for Chinese) to English.

It features advanced features such as remembering both the translated names of characters (their genders too!), abilities, things and abilities by tracking these entities over each chapter and telling the model the correct translation if a particular entity is inside the new chapter.

When a new entity shows up in a chapter being translated, you will enter an interactive mode that looks like below, allowing you to update the entity's attributes:

Totally New Entities In This Chapter:
{
    "characters": {
        "马泰尔": {
            "translation": "Mateer",
            "gender": "male",
            "count": 1,
            "last_chapter": "THIS CHAPTER"
        }
    },
    "places": {},
    "organizations": {},
    "abilities": {},
    "equipment": {}
}

Do you want to make any changes? (yes to proceed, no to exit): yes

Categories:
  1. characters
  2. places
  3. organizations
  4. abilities
  5. equipment

Select a category to edit (enter number, or press Enter to stop): 1

Items in 'characters':
  1. 马泰尔 (Mateer)

Select an item to edit (enter number, or press Enter to stop): 1

Editing item: 马泰尔

Do you want to ask the LLM for translation options? (yes to proceed, no to exit): yes
gpt-4o says, "The untranslated Chinese characters '马泰尔' sound like 'Ma-Tai-Er' which is a phonetic approximation, suitable for names. Different Romanization can capture this phonetic nuance. Here are some alternative translations."
  1. Martel
  2. Matel
  3. Mattel
  4. Custom Translation [Your Input]

Select an item to edit (enter number, or press Enter to keep original translation): 1

Do you want to delete '马泰尔'? (yes or y to delete, no or enter to keep): 
