use AppleScript version "2.5"
use framework "Foundation"
use scripting additions

-- Fixed individual-task operations only. Inputs are an owner-only temporary
-- JSON file. No shell, arbitrary script, status, deletion or recurrence writes.
on run argv
    if (count of argv) is not 1 then error "Expected private request file"
    set bytes to current application's NSData's dataWithContentsOfFile:(item 1 of argv)
    if bytes is missing value then error "Request unavailable"
    set request to current application's NSJSONSerialization's JSONObjectWithData:bytes options:0 |error|:(missing value)
    if request is missing value then error "Invalid request"
    set change to request's objectForKey:"change"
    set expectedContext to request's objectForKey:"context"
    set expected to request's objectForKey:"baseline"
    set operationName to (change's objectForKey:"operation") as text
    set newTitle to change's objectForKey:"title"
    set newStart to change's objectForKey:"start_date"
    set newNotes to change's objectForKey:"notes"
    set newDeadline to change's objectForKey:"deadline"
    set hasDeadline to (change's allKeys()'s containsObject:"deadline") as boolean
    set clearDeadline to false
    if hasDeadline then set clearDeadline to (newDeadline's isEqual:(current application's NSNull's |null|())) as boolean
    set attemptedChange to false
    set changedID to missing value
    set outcomeName to "not_started"
    try
        set currentZone to (current application's NSTimeZone's localTimeZone()'s |name|()) as text
        if currentZone is not ((expectedContext's objectForKey:"time_zone") as text) then error "Context changed"
        if newStart is not missing value then
            set startDay to my calendarDay(newStart as text)
        end if
        if hasDeadline and not clearDeadline then set deadlineDay to my calendarDay(newDeadline as text)
        with timeout of 60 seconds
            tell application "/Applications/Things3.app"
                if version is not ((expectedContext's objectForKey:"app_version") as text) then error "Context changed"
                if operationName is "update" then
                    set requestedID to (change's objectForKey:"task_id") as text
                    set taskRef to to do id requestedID
                    if class of taskRef is project then error "Project outside task scope"
                    if not (my matchesSnapshot(taskRef, expected's objectForKey:"item")) then error "Item changed"
                    set changedID to id of taskRef
                    if newTitle is not missing value then
                        set attemptedChange to true
                        set name of taskRef to (newTitle as text)
                    end if
                else if operationName is "create" then
                    set destinationID to change's objectForKey:"project_id"
                    if destinationID is missing value then
                        set attemptedChange to true
                        set taskRef to make new to do with properties {name:(newTitle as text)} at beginning of list id "TMInboxListSource"
                    else
                        set destinationRef to project id (destinationID as text)
                        if not (my matchesSnapshot(destinationRef, expected's objectForKey:"item")) then error "Project changed"
                        set attemptedChange to true
                        set taskRef to make new to do with properties {name:(newTitle as text)} at beginning of destinationRef
                    end if
                    set changedID to id of taskRef
                else
                    error "Unsupported operation"
                end if
                if newStart is not missing value then
                    set attemptedChange to true
                    schedule taskRef for startDay
                end if
                if newNotes is not missing value then
                    set attemptedChange to true
                    set notes of taskRef to (newNotes as text)
                end if
                if hasDeadline then
                    set attemptedChange to true
                    if clearDeadline then
                        set due date of taskRef to missing value
                    else
                        set due date of taskRef to deadlineDay
                    end if
                end if
                set outcomeName to "applied"
            end tell
        end timeout
    on error
        if attemptedChange then set outcomeName to "uncertain"
    end try
    set output to current application's NSMutableDictionary's alloc()'s init()
    output's setObject:outcomeName forKey:"outcome"
    if changedID is missing value then
        output's setObject:(current application's NSNull's |null|()) forKey:"id"
    else
        output's setObject:changedID forKey:"id"
    end if
    set jsonData to current application's NSJSONSerialization's dataWithJSONObject:output options:0 |error|:(missing value)
    return (current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)) as text
end run

on calendarDay(dayText)
    set localDay to (current date)
    set time of localDay to 0
    set day of localDay to 1
    set year of localDay to ((text 1 thru 4 of dayText) as integer)
    set month of localDay to ((text 6 thru 7 of dayText) as integer)
    set day of localDay to ((text 9 thru 10 of dayText) as integer)
    return localDay
end calendarDay

on matchesSnapshot(itemRef, expected)
    set row to current application's NSMutableDictionary's alloc()'s init()
    tell application "/Applications/Things3.app"
        if status of itemRef is not open then return false
        row's setObject:(id of itemRef) forKey:"id"
        row's setObject:(name of itemRef) forKey:"title"
        set taskNotes to notes of itemRef
        if taskNotes is missing value then set taskNotes to ""
        row's setObject:taskNotes forKey:"notes"
        row's setObject:"open" forKey:"status"
        set parentProject to project of itemRef
        if parentProject is missing value then
            row's setObject:(current application's NSNull's |null|()) forKey:"project"
        else
            set parentDict to current application's NSMutableDictionary's alloc()'s init()
            parentDict's setObject:(id of parentProject) forKey:"id"
            parentDict's setObject:(name of parentProject) forKey:"title"
            row's setObject:parentDict forKey:"project"
        end if
        row's setObject:(my dateValue(activation date of itemRef, true)) forKey:"scheduled_date"
        row's setObject:(my dateValue(due date of itemRef, true)) forKey:"deadline"
        row's setObject:(my dateValue(modification date of itemRef, false)) forKey:"modified_at"
    end tell
    return (row's isEqualToDictionary:expected) as boolean
end matchesSnapshot

on dateValue(theDate, dateOnly)
    if theDate is missing value then return current application's NSNull's |null|()
    if dateOnly then
        set formatter to current application's NSDateFormatter's alloc()'s init()
        formatter's setLocale:(current application's NSLocale's localeWithLocaleIdentifier:"en_US_POSIX")
        formatter's setDateFormat:"yyyy-MM-dd"
    else
        set formatter to current application's NSISO8601DateFormatter's alloc()'s init()
    end if
    return (formatter's stringFromDate:theDate) as text
end dateValue
