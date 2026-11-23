Feature: The dashboard knows which services Dobby is connected to
  Composio holds Dobby's connections, so the service-accounts page asks Composio
  instead of trusting only the dashboard's own records.

  Background:
    Given I am signed in as an admin

  Scenario: A connection made outside the dashboard shows as connected
    Given the dashboard has no record of any connection
    And Composio has a "ACTIVE" "googlecalendar" account for Dobby
    When I open the service accounts page
    Then "google_calendar" shows as "connected"
    And "notion" shows as "disconnected"
    And every status is verified with Composio

  Scenario: A recorded connection that expired in Composio needs a reconnect
    Given the dashboard has a record for "instagram"
    And Composio has a "EXPIRED" "instagram" account for Dobby
    When I open the service accounts page
    Then "instagram" shows as "needs_reconnect"

  Scenario: A recorded connection that was removed in Composio shows as disconnected
    Given the dashboard has a record for "linkedin"
    And Composio has no accounts for Dobby
    When I open the service accounts page
    Then "linkedin" shows as "disconnected"

  Scenario: A sign-in that is still in progress shows as pending
    Given the dashboard has no record of any connection
    And Composio has a "INITIATED" "notion" account for Dobby
    When I open the service accounts page
    Then "notion" shows as "pending"

  Scenario: The healthiest of several accounts decides the status
    Given the dashboard has no record of any connection
    And Composio has a "EXPIRED" "notion" account for Dobby
    And Composio has a "ACTIVE" "notion" account for Dobby
    When I open the service accounts page
    Then "notion" shows as "connected"

  Scenario: Revoked and disabled accounts don't count
    Given the dashboard has no record of any connection
    And Composio has a "REVOKED" "linkedin" account for Dobby
    And Composio has a disabled "ACTIVE" "instagram" account for Dobby
    When I open the service accounts page
    Then "linkedin" shows as "disconnected"
    And "instagram" shows as "disconnected"

  Scenario: Accounts on later Composio pages are found
    Given the dashboard has no record of any connection
    And Composio returns "googlecalendar" on page one and "notion" on page two
    When I open the service accounts page
    Then "google_calendar" shows as "connected"
    And "notion" shows as "connected"

  Scenario: When Composio is unreachable the last known state is shown, unverified
    Given the dashboard has a record for "notion"
    And Composio is unreachable
    When I open the service accounts page
    Then "notion" shows as "connected"
    And "google_calendar" shows as "disconnected"
    And no status is verified with Composio

  Scenario: Students cannot see Dobby's service accounts
    Given I am signed in as a student instead
    When I open the service accounts page
    Then the request is refused with 403
