# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Allow plan switching when SingleRecurringSubscription validator is enabled

### Fixed

- Fix subscriptions cancellation
- Set auto_prolong flag correctly after google RTDN notification
- Fix charge_offline not able to find subscription_id in reference payment metadata

## [1.0.4] - 2023-07-04

### Fixed

- Fix migrations if non-default database is used

## [1.0.3] - 2023-07-01

### Fixed

- Fix Paddle double-charge
