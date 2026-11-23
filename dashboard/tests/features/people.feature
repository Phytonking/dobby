Feature: People and their emails
  Teammates keep the address Dobby invites them with up to date, and admins add
  teammates whether they sign in with Google, Discord or a local account.

  Scenario: A teammate saves their calendar email
    Given I am signed in as a student
    When I save my calendar email as "  Jane.Doe@Example.ORG "
    Then the request succeeds
    And my calendar email is stored as "jane.doe@example.org"

  Scenario: A teammate clears their calendar email
    Given I am signed in as a student
    When I clear my calendar email
    Then the request succeeds
    And my calendar email is cleared

  Scenario: A malformed calendar email is refused with a readable reason
    Given I am signed in as a student
    When I save my calendar email as "jane@nowhere"
    Then the request is refused with 422
    And the reason mentions "valid email address"
    And nothing is saved

  Scenario: An admin adds a teammate who only uses Discord
    Given I am signed in as an admin
    When I add "Sam" with Discord ID "123456789012345678" and no Google email
    Then the user is created
    And the new user has no Google email

  Scenario: An admin's mixed-case Google email is stored lowercased
    Given I am signed in as an admin
    When I add "Jane" with Google email "Jane@Gmail.COM"
    Then the user is created
    And the new user's Google email is "jane@gmail.com"

  Scenario: A teammate needs some way to be recognised
    Given I am signed in as an admin
    When I add "Nobody" with no Google email and no Discord ID
    Then the request is refused with 422
    And the reason mentions "Google email or a Discord ID"

  Scenario: A Discord ID must be the numeric user ID
    Given I am signed in as an admin
    When I add "Sam" with Discord ID "sam#1234" and no Google email
    Then the request is refused with 422
    And the reason mentions "numeric Discord user ID"

  Scenario: Google sign-in finds a teammate whatever case their email was typed in
    When someone signs in with Google as "Jane@Gmail.com"
    Then the user lookup compares emails case-insensitively
