DOMAIN = "mymeal"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_TOKEN = "token"
CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_HOST = "http://127.0.0.1"
DEFAULT_PORT = 7850
DEFAULT_SUMMARY_PATH = "/api/v1/ha/summary"
DEFAULT_CALENDAR_PATH = "/api/v1/ha/calendar"
DEFAULT_UPDATE_INTERVAL = 300

INTENT_WHATS_FOR_DINNER = "MyMealWhatsForDinner"
INTENT_WHAT_CAN_I_COOK = "MyMealWhatCanICook"
INTENT_ADD_TO_LIST = "MyMealAddToShoppingList"

SERVICE_WHATS_FOR_DINNER = "whats_for_dinner"
SERVICE_WHAT_CAN_I_COOK = "what_can_i_cook"
SERVICE_ADD_TO_LIST = "add_to_shopping_list"
SERVICE_PLAN_WEEK = "plan_week"

# Recipe CRUD + versions/experiments. These register automatically with the
# (auto-discovered) integration, so they work with no extra setup — unlike the
# MCP server, which needs an MCP client pointed at it by hand.
SERVICE_SEARCH_RECIPES = "search_recipes"
SERVICE_GET_RECIPE = "get_recipe"
SERVICE_ADD_RECIPE = "add_recipe"
SERVICE_UPDATE_RECIPE = "update_recipe"
SERVICE_DELETE_RECIPE = "delete_recipe"
SERVICE_LIST_RECIPE_VERSIONS = "list_recipe_versions"
SERVICE_START_RECIPE_EXPERIMENT = "start_recipe_experiment"
SERVICE_ADD_EXPERIMENT_FEEDBACK = "add_experiment_feedback"
SERVICE_PROMOTE_EXPERIMENT = "promote_experiment"
SERVICE_RESTORE_RECIPE_VERSION = "restore_recipe_version"
