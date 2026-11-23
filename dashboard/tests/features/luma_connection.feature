Feature: An admin connects Dobby to Luma's MCP server
  Luma isn't on Composio, so the dashboard runs Luma's OAuth sign-in itself
  (authorization code + PKCE, client identified by a metadata document URL) and
  keeps the tokens for the bot.

  Background:
    Given the API's public address is "https://dobby.example.org/api"
    And Luma publishes its OAuth details

  Scenario: Connecting sends the admin to Luma's consent screen with PKCE
    Given I am signed in as an admin
    When I start connecting Luma
    Then I am sent to Luma's consent screen
    And the request identifies Dobby by "https://dobby.example.org/api/integrations/luma/client.json"
    And the request returns to "https://dobby.example.org/api/integrations/luma/callback"
    And the request asks for the "https://mcp.luma.com" resource with an S256 code challenge

  Scenario: Finishing sign-in stores Dobby's Luma tokens
    Given I am signed in as an admin
    And I have started connecting Luma
    When Luma sends me back with a code
    Then Dobby traded the code using the matching PKCE verifier
    And Dobby's Luma tokens are stored, expiring in about an hour
    And I land on the service accounts page with "connected=luma"

  Scenario: A callback that doesn't match the sign-in Dobby started is ignored
    Given I am signed in as an admin
    And I have started connecting Luma
    When Luma sends me back with a code but a different state
    Then I land on the service accounts page with "error=luma"
    And Dobby never asked Luma for tokens

  Scenario: A callback naming a different issuer is ignored
    Given I am signed in as an admin
    And I have started connecting Luma
    When Luma sends me back with a code from issuer "https://evil.example"
    Then I land on the service accounts page with "error=luma"
    And Dobby never asked Luma for tokens

  Scenario: Declining on Luma's consent screen stores nothing
    Given I am signed in as an admin
    And I have started connecting Luma
    When Luma sends me back with error "access_denied"
    Then I land on the service accounts page with "error=luma"
    And nothing is stored

  Scenario: Luma refusing the code stores nothing
    Given I am signed in as an admin
    And I have started connecting Luma
    And Luma will refuse the code
    When Luma sends me back with a code
    Then I land on the service accounts page with "error=luma"
    And nothing is stored

  Scenario: Disconnecting revokes Dobby's tokens and forgets them
    Given I am signed in as an admin
    And Dobby is connected to Luma
    When I disconnect Luma
    Then Luma was asked to revoke the refresh token and the access token
    And the Luma connection is deleted

  Scenario: Luma can fetch Dobby's client metadata without signing in
    When Luma fetches Dobby's client metadata
    Then it names client "https://dobby.example.org/api/integrations/luma/client.json" returning to "https://dobby.example.org/api/integrations/luma/callback"
    And it is a public client using the authorization code and refresh token grants

  Scenario: A separately hosted client metadata document can be used
    Given LUMA_CLIENT_ID is "https://example.github.io/dobby/luma-client.json"
    And I am signed in as an admin
    When I start connecting Luma
    Then the request identifies Dobby by "https://example.github.io/dobby/luma-client.json"

  Scenario: Students cannot connect Luma
    Given I am signed in as a student
    When I start connecting Luma
    Then the request is refused with 403
