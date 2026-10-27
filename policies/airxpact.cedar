// Replace GATEWAY_ARN with the ARN of the AgentCore Gateway before creating the policies.

// allow_airxpact_tools
permit (
  principal,
  action in [
    AgentCore::Action::"AviationTools___get_aviation_weather",
    AgentCore::Action::"AviationTools___get_destination_forecast",
    AgentCore::Action::"AviationTools___search_flights",
    AgentCore::Action::"AviationTools___plan_trip_timeline",
    AgentCore::Action::"AviationTools___create_calendar_file",
    AgentCore::Action::"AviationTools___get_country_info",
    AgentCore::Action::"AviationTools___get_exchange_rate",
    AgentCore::Action::"AviationTools___search_ground_transport",
    AgentCore::Action::"AviationTools___get_flight_status",
    AgentCore::Action::"AviationTools___create_booking_request",
    AgentCore::Action::"AviationTools___get_booking_status",
    AgentCore::Action::"PlacesSearch___searchPlaces",
    AgentCore::Action::"WebSearch___searchWeb"
  ],
  resource == AgentCore::Gateway::"GATEWAY_ARN"
);

// limit_group_flight_search
forbid (
  principal,
  action == AgentCore::Action::"AviationTools___search_flights",
  resource == AgentCore::Gateway::"GATEWAY_ARN"
)
when { context.input has adults && context.input.adults > 9 };

// limit_flight_results
forbid (
  principal,
  action == AgentCore::Action::"AviationTools___search_flights",
  resource == AgentCore::Gateway::"GATEWAY_ARN"
)
when { context.input has max_results && context.input.max_results > 10 };

// limit_group_bookings
forbid (
  principal,
  action == AgentCore::Action::"AviationTools___create_booking_request",
  resource == AgentCore::Gateway::"GATEWAY_ARN"
)
when { context.input has passengers && context.input.passengers > 9 };

// limit_web_search_size
forbid (
  principal,
  action == AgentCore::Action::"WebSearch___searchWeb",
  resource == AgentCore::Gateway::"GATEWAY_ARN"
)
when { context.input has max_results && context.input.max_results > 8 };
