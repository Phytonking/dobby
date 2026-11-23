Feature: Admins see the health of every system over the last 24 hours

  Background:
    Given I am signed in as an admin

  Scenario: Calls and failures are counted per service
    Given Composio has a "ACTIVE" "googlecalendar" account for Dobby
    And Composio has a "EXPIRED" "notion" account for Dobby
    And these tool calls in the last 24 hours:
      | tool                        | status | minutes_ago |
      | GOOGLECALENDAR_CREATE_EVENT | ok     | 5           |
      | google_calendar.invite      | ok     | 90          |
      | NOTION_SEARCH_NOTION_PAGE   | error  | 30          |
      | lookup_calendar_email       | error  | 10          |
      | lookup_calendar_email       | ok     | 200         |
    When I open the system health page
    Then there were 5 calls and 2 failures in total
    And "google_calendar" had 2 calls and 0 failures and is "connected"
    And "notion" had 1 calls and 1 failures and is "needs_reconnect"
    And "dobby" had 2 calls and 1 failures
    And "instagram" had 0 calls and 0 failures and is "disconnected"
    And the hourly chart has 24 hours adding up to 5 calls
    And the most recent hour shows 1 ok and 1 failed

  Scenario: A quiet day still reports every service
    Given Composio has no accounts for Dobby
    And no tool calls in the last 24 hours
    When I open the system health page
    Then there were 0 calls and 0 failures in total
    And every service is listed
    And there is no last activity

  Scenario: Students cannot see system health
    Given I am signed in as a student instead
    When I open the system health page
    Then the request is refused with 403
