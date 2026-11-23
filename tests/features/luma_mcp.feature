Feature: Dobby works in Luma through Luma's MCP server
  Dobby asks Luma's MCP server what it can do and runs those tools. Anything that
  only reads runs straight away; anything that changes Luma waits for the
  requester's Confirm, and runs with exactly the arguments that were previewed.

  Background:
    Given Luma is connected
    And Luma's MCP server offers a read-only "list_events" tool and an unannotated "send_invites" tool

  Scenario Outline: Dobby learns what Luma can do
    Given Luma's MCP server answers with <format>
    When Dobby asks which Luma tools there are
    Then Dobby is offered "list_events" as read-only
    And Dobby is offered "send_invites" as a change

    Examples:
      | format             |
      | an SSE stream      |
      | plain JSON         |

  Scenario: Reading from Luma happens straight away
    When Dobby calls "list_events" with '{"limit": 1}'
    Then the call succeeds with "Demo Night"
    And nothing is waiting for Confirm
    And Luma ran "list_events" 1 time

  Scenario: Changing Luma waits for Confirm
    When Dobby calls "send_invites" with '{"event_id": "evt-1", "emails": ["sam@example.org"]}'
    Then the change is waiting for Confirm with a preview showing "sam@example.org"
    And Luma ran "send_invites" 0 times
    When the requester confirms
    Then Luma ran "send_invites" 1 time with '{"event_id": "evt-1", "emails": ["sam@example.org"]}'
    And the confirmed change reports "invited 1 to evt-1"

  Scenario: A tool Luma doesn't have is refused
    When Dobby calls "delete_everything" with '{}'
    Then the call fails mentioning "luma_tools"

  Scenario: Arguments that aren't a JSON object are refused
    When Dobby calls "list_events" with 'not json'
    Then the call fails mentioning "JSON object"

  Scenario: An error from a Luma tool is reported, not hidden
    When Dobby calls "list_events" with '{"limit": -1}'
    Then the call fails mentioning "limit must be positive"

  Scenario: An expired sign-in is refreshed once and the call retried
    Given Luma no longer accepts Dobby's current access token
    When Dobby calls "list_events" with '{"limit": 1}'
    Then the call succeeds with "Demo Night"
    And Dobby refreshed its Luma sign-in 1 time

  Scenario: A refresh Luma rejects asks an admin to reconnect
    Given Luma no longer accepts Dobby's current access token
    And Luma rejects Dobby's refresh token
    When Dobby calls "list_events" with '{"limit": 1}'
    Then the call fails mentioning "reconnect Luma"

  Scenario: Without a Luma connection Dobby says how to get one
    Given Luma is not connected
    When Dobby asks which Luma tools there are
    Then the call fails mentioning "connect it on the dashboard"
