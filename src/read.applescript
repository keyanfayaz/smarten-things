use AppleScript version "2.5"
use framework "Foundation"
use scripting additions

-- Derived from historical/scan.applescript. Only explicit public reads.
-- Keep all Things requests visible; never use the properties record.
on run argv
    if (count of argv) is not in {4, 5} then error "Expected kind, container, offset, limit, optional metadata"
    set includeMetadata to false
    if (count of argv) is 5 then
        if item 5 of argv is not "metadata" then error "Unsupported schema"
        set includeMetadata to true
    end if
    set readKind to item 1 of argv
    set containerID to item 2 of argv
    set pageOffset to (item 3 of argv) as integer
    set pageLimit to (item 4 of argv) as integer
    if pageOffset < 0 or pageOffset > 1000000 then error "Offset outside allowed range"
    if pageLimit < 1 or pageLimit > 50 then error "Limit outside allowed range"
    if readKind is not in {"list", "project", "projects"} then error "Unsupported read kind"
    if readKind is "list" and containerID is not in {"TMInboxListSource", "TMTodayListSource", "TMCalendarListSource"} then error "List outside read scope"
    if readKind is "projects" and containerID is not "open-projects" then error "Catalog outside read scope"

    set rows to current application's NSMutableArray's alloc()'s init()
    set resultDict to current application's NSMutableDictionary's alloc()'s init()
    with timeout of 60 seconds
        tell application "/Applications/Things3.app"
            set appVersion to version
            if readKind is "projects" then
                set containerName to "Open projects"
                set eligible to every project whose status is open
            else
                if readKind is "list" then
                    set containerRef to list id containerID
                else
                    set containerRef to project id containerID
                    if status of containerRef is not open then error "Closed project outside read scope"
                end if
                set containerName to name of containerRef
                set eligible to to dos of containerRef whose status is open
            end if
            set totalCount to count of eligible
            set lastIndex to pageOffset + pageLimit
            if lastIndex > totalCount then set lastIndex to totalCount
            if pageOffset < totalCount then
                repeat with taskIndex from (pageOffset + 1) to lastIndex
                    set taskRef to item taskIndex of eligible
                    -- Recheck status if the collection changed during the read.
                    if status of taskRef is not open then error "Collection changed; discard page"
                    set taskID to id of taskRef
                    set taskTitle to name of taskRef
                    set row to current application's NSMutableDictionary's alloc()'s init()
                    row's setObject:taskID forKey:"id"
                    row's setObject:taskTitle forKey:"title"
                    row's setObject:"open" forKey:"status"
                    if includeMetadata then
                        -- These public fields are verified against the installed sdef by Python.
                        set taskProject to project of taskRef
                        set taskSchedule to activation date of taskRef
                        set taskDeadline to due date of taskRef
                        set taskModified to modification date of taskRef
                        if taskProject is missing value then
                            row's setObject:(current application's NSNull's |null|()) forKey:"project"
                        else
                            set projectDict to current application's NSMutableDictionary's alloc()'s init()
                            projectDict's setObject:(id of taskProject) forKey:"id"
                            projectDict's setObject:(name of taskProject) forKey:"title"
                            row's setObject:projectDict forKey:"project"
                        end if
                        row's setObject:(my dateValue(taskSchedule, true)) forKey:"scheduled_date"
                        row's setObject:(my dateValue(taskDeadline, true)) forKey:"deadline"
                        row's setObject:(my dateValue(taskModified, false)) forKey:"modified_at"
                    end if
                    rows's addObject:row
                end repeat
            end if
        end tell
    end timeout
    set returnedCount to (rows's |count|()) as integer
    set nextOffset to pageOffset + returnedCount
    set moreRows to nextOffset < totalCount
    resultDict's setObject:1 forKey:"schema_version"
    if includeMetadata then
        resultDict's setObject:2 forKey:"schema_version"
        resultDict's setObject:"things" forKey:"source"
        resultDict's setObject:{"priority", "progress_history", "when_date", "recurrence"} forKey:"unsupported_fields"
    end if
    resultDict's setObject:readKind forKey:"kind"
    resultDict's setObject:containerID forKey:"container_id"
    resultDict's setObject:containerName forKey:"container_name"
    resultDict's setObject:appVersion forKey:"app_version"
    resultDict's setObject:pageOffset forKey:"offset"
    resultDict's setObject:pageLimit forKey:"limit"
    resultDict's setObject:totalCount forKey:"total_count"
    resultDict's setObject:returnedCount forKey:"returned_count"
    resultDict's setObject:(current application's NSNumber's numberWithBool:moreRows) forKey:"has_more"
    if moreRows then
        resultDict's setObject:nextOffset forKey:"next_offset"
    else
        resultDict's setObject:(current application's NSNull's |null|()) forKey:"next_offset"
    end if
    resultDict's setObject:rows forKey:"items"
    set jsonData to current application's NSJSONSerialization's dataWithJSONObject:resultDict options:0 |error|:(missing value)
    if jsonData is missing value then error "JSON serialization failed"
    return (current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)) as text
end run

on dateValue(theDate, dateOnly)
    if theDate is missing value then return current application's NSNull's |null|()
    if dateOnly then
        -- Scheduling/deadlines are calendar days in the Mac's local time zone.
        set formatter to current application's NSDateFormatter's alloc()'s init()
        formatter's setLocale:(current application's NSLocale's localeWithLocaleIdentifier:"en_US_POSIX")
        formatter's setDateFormat:"yyyy-MM-dd"
    else
        set formatter to current application's NSISO8601DateFormatter's alloc()'s init()
    end if
    return (formatter's stringFromDate:theDate) as text
end dateValue
